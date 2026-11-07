"""Agent API、SSE 和第一阶段对话入口的并存。"""

from __future__ import annotations

import asyncio
import threading
import time

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_settings
from tests.fakes import FakeProvider, chat_payload, make_result, make_tool_call

_TERMINAL = {"completed", "failed", "cancelled", "limit_reached"}


def _wait(client: TestClient, task_id: str) -> dict[str, object]:
    last: dict[str, object] = {}
    for _ in range(80):
        response = client.get(f"/api/v1/agents/runs/{task_id}")
        assert response.status_code == 200
        last = response.json()
        if last["status"] in _TERMINAL:
            return last
        time.sleep(0.02)
    raise AssertionError(last)


def test_tool_round_trip_and_phase1_chat_remain_separate(client: TestClient, provider: FakeProvider) -> None:
    provider.push(make_result("旧接口回答"))
    chat = client.post("/api/v1/chat/completions", json=chat_payload())
    assert chat.status_code == 200
    assert chat.json()["choices"][0]["message"]["content"] == "旧接口回答"
    assert chat.json()["finish_reason"] == "stop"

    provider.push(
        make_result(
            content=None,
            tool_calls=[make_tool_call()],
            finish_reason="tool_calls",
            reasoning="secret-thought",
        )
    )
    provider.push(make_result("结果是 437"))
    created = client.post(
        "/api/v1/agents/runs",
        json={"message": "计算 23 * 19", "prompt_name": "calculator"},
    )
    assert created.status_code == 200
    task_id = created.json()["task_id"]
    finished = _wait(client, task_id)
    assert finished["status"] == "completed"
    assert finished["answer"] == "结果是 437"
    assert finished["tool_calls"] == 1
    assert "secret-thought" not in created.text
    assert "secret-thought" not in str(finished)
    follow = provider.calls[-1]
    assert any(item.get("role") == "tool" and "437" in item.get("content", "") for item in follow.messages)
    assert follow.thinking == {"type": "disabled"}


def test_client_cannot_submit_tools_or_raise_limits(client: TestClient, provider: FakeProvider) -> None:
    rejected = client.post(
        "/api/v1/agents/runs",
        json={"message": "计算", "prompt_name": "calculator", "tools": [{"name": "shell"}], "handler": "os.system"},
    )
    assert rejected.status_code == 422
    widened = client.post(
        "/api/v1/agents/runs",
        json={"message": "计算", "prompt_name": "calculator", "policy": {"max_steps": 100}},
    )
    assert widened.status_code == 422
    assert widened.json()["error"]["code"] == "AGENT_POLICY_INVALID"
    missing = client.post(
        "/api/v1/agents/runs",
        json={"message": "计算", "prompt_name": "calculator", "allowed_tools": ["shell"]},
    )
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "AGENT_TOOL_NOT_ALLOWED"
    assert provider.calls == []


def test_sse_delivers_terminal_without_reasoning(client: TestClient, provider: FakeProvider) -> None:
    provider.push(make_result("流式终态", reasoning="secret-thought"))
    created = client.post(
        "/api/v1/agents/runs",
        json={"message": "只回答", "prompt_name": "default"},
    )
    task_id = created.json()["task_id"]
    with client.stream("GET", f"/api/v1/agents/runs/{task_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        payload = response.read().decode("utf-8")
    assert "event: agent.completed" in payload
    assert "secret-thought" not in payload
    assert _wait(client, task_id)["status"] == "completed"


def test_deadline_cancel_and_disconnect(client: TestClient, provider: FakeProvider) -> None:
    async def slow(request):
        provider.calls.append(request)
        await asyncio.sleep(2)
        return make_result("LATE_SUCCESS")

    provider.create_chat_completion = slow  # type: ignore[method-assign]
    created = client.post(
        "/api/v1/agents/runs",
        json={
            "message": "慢请求",
            "prompt_name": "calculator",
            "policy": {"max_wall_time_seconds": 0.15},
        },
    )
    finished = _wait(client, created.json()["task_id"])
    assert finished["status"] == "limit_reached"
    assert finished["error_code"] == "AGENT_DEADLINE_EXCEEDED"
    assert finished["answer"] is None

    release = threading.Event()
    entered = threading.Event()

    async def blocked(request):
        provider.calls.append(request)
        entered.set()
        await asyncio.to_thread(release.wait)
        return make_result("取消后的成功")

    provider.create_chat_completion = blocked  # type: ignore[method-assign]
    running = client.post(
        "/api/v1/agents/runs",
        json={"message": "请取消", "prompt_name": "calculator"},
    )
    task_id = running.json()["task_id"]
    assert entered.wait(timeout=2)
    with client.stream("GET", f"/api/v1/agents/runs/{task_id}/events") as stream:
        assert stream.status_code == 200
    still_running = client.get(f"/api/v1/agents/runs/{task_id}").json()
    assert still_running["status"] in {"created", "running"}
    cancelled = client.post(f"/api/v1/agents/runs/{task_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    repeated = client.post(f"/api/v1/agents/runs/{task_id}/cancel")
    assert repeated.json()["status"] == "cancelled"
    release.set()
    missing = client.get("/api/v1/agents/runs/not a task")
    assert missing.status_code == 404


def test_custom_runtime_settings_still_serve_health() -> None:
    app = create_app(settings=make_settings(agent_max_active_tasks=1), provider=FakeProvider())
    with TestClient(app) as client:
        ready = client.get("/ready")
        assert ready.status_code == 200


_M3_KEYS = {
    "task_id",
    "request_id",
    "status",
    "answer",
    "steps",
    "tool_calls",
    "error_code",
    "usage",
}


def test_default_agent_response_matches_m3_fields(client: TestClient, provider: FakeProvider) -> None:
    provider.push(make_result("原样回答"))
    created = client.post(
        "/api/v1/agents/runs",
        json={"message": "只回答", "prompt_name": "default"},
    )
    assert created.status_code == 200
    assert set(created.json()) == _M3_KEYS
    finished = _wait(client, created.json()["task_id"])
    assert finished["status"] == "completed"
    assert finished["answer"] == "原样回答"
    assert set(finished) == _M3_KEYS


def test_unknown_and_offline_toolsets_use_new_error_codes(client: TestClient) -> None:
    unknown = client.post(
        "/api/v1/agents/runs",
        json={"message": "搜索", "prompt_name": "default", "toolsets": ["mcp"]},
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "TOOLSET_UNKNOWN"
    offline = client.post(
        "/api/v1/agents/runs",
        json={"message": "搜索", "prompt_name": "default", "toolsets": ["research"]},
    )
    assert offline.status_code == 403
    assert offline.json()["error"]["code"] == "TOOLSET_UNAVAILABLE"
