"""真实 DeepSeek 联调。默认跳过，设置 DEEPSEEK_LIVE=1 且提供 DEEPSEEK_API_KEY 后才请求上游。

测试输出会去掉密钥，不要把密钥写进断言信息。
"""

from __future__ import annotations

import logging
import os
import re
import time

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

pytestmark = pytest.mark.skipif(
    os.environ.get("DEEPSEEK_LIVE") != "1",
    reason="设置 DEEPSEEK_LIVE=1 才会请求真实 DeepSeek",
)


def _key() -> str:
    return os.environ.get("DEEPSEEK_API_KEY", "").strip()


def _scrub(text: str) -> str:
    secret = _key()
    if secret:
        text = text.replace(secret, "***")
    return re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "sk-***", text)


def _settings(**overrides: object) -> Settings:
    if not _key():
        pytest.fail("真实联调未完成：环境变量 DEEPSEEK_API_KEY 为空")
    values: dict[str, object] = {
        "deepseek_api_key": _key(),
        "deepseek_base_url": "https://api.deepseek.com",
        "deepseek_model": "deepseek-flash",
        "deepseek_model_allowlist": "deepseek-flash,deepseek-v4-pro",
        "deepseek_timeout_seconds": 45,
        "deepseek_max_retries": 0,
        "deepseek_retry_backoff_seconds": 0,
        "deepseek_thinking": "disabled",
        "log_level": "INFO",
        "expose_reasoning": False,
        "max_tool_rounds": 3,
        "tool_round_hard_limit": 5,
        "tool_timeout_seconds": 2,
        "tool_result_max_chars": 4000,
        "require_api_key_on_startup": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _assert_no_secret(*chunks: str) -> None:
    secret = _key()
    combined = "\n".join(chunks)
    leaked_value = bool(secret) and secret in combined
    leaked_pattern = re.search(r"sk-[A-Za-z0-9_\-]{8,}", combined) is not None
    leaked_bearer = re.search(r"(?i)authorization:\s*bearer\s+\S+", combined) is not None
    assert leaked_value is False
    assert leaked_pattern is False
    assert leaked_bearer is False


@pytest.fixture
def live_logs(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO, logger="muzi_llm")
    return caplog


def test_live_chat_completion_and_schema(live_logs: pytest.LogCaptureFixture) -> None:
    settings = _settings()
    app = create_app(settings=settings)
    with TestClient(app) as client:
        provider = app.state.provider
        base_url = str(provider.client.base_url).rstrip("/")
        assert base_url == "https://api.deepseek.com"
        assert "chat/completions" not in base_url
        response = client.post(
            "/api/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "只回复一个词：pong"}],
                "prompt_name": "default",
                "temperature": 0,
                "max_tokens": 64,
                "thinking": {"type": "disabled"},
            },
        )
        openapi = client.get("/openapi.json").json()
    assert response.status_code == 200, _scrub(response.text)
    body = response.json()
    message = body["choices"][0]["message"]
    assert body["model"]
    assert body["id"]
    assert body["request_id"]
    assert body["finish_reason"] in {"stop", "length"}
    assert message["role"] == "assistant"
    assert message["content"].strip()
    assert "reasoning_content" not in message
    assert body["usage"]["total_tokens"] > 0
    schema_props = openapi["components"]["schemas"]["ChatCompletionResponse"]["properties"]
    assert set(body) <= set(schema_props)
    _assert_no_secret(response.text, live_logs.text)
    assert provider.client is None


def test_live_sse_streams_until_done(live_logs: pytest.LogCaptureFixture) -> None:
    app = create_app(settings=_settings())
    payload = {
        "messages": [{"role": "user", "content": "用两句简体中文介绍 HTTP。不要使用工具。"}],
        "prompt_name": "default",
        "temperature": 0,
        "max_tokens": 128,
        "thinking": {"type": "disabled"},
    }
    with TestClient(app) as client:
        with client.stream("POST", "/api/v1/chat/completions/stream", json=payload) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            raw = response.read().decode("utf-8")
    assert raw.count("event: delta") >= 1
    assert "event: done" in raw
    assert "event: error" not in raw
    assert "event: usage" in raw
    assert '"finish_reason":"stop"' in raw or '"finish_reason":"length"' in raw
    _assert_no_secret(raw, live_logs.text)


def test_live_tool_loop_executes_and_answers(live_logs: pytest.LogCaptureFixture) -> None:
    app = create_app(settings=_settings())
    payload = {
        "messages": [
            {
                "role": "user",
                "content": "请调用 calculator 计算 23 * 19。不要心算，最终答案必须使用工具返回的数字。",
            }
        ],
        "prompt_name": "calculator",
        "temperature": 0,
        "max_tokens": 256,
        "max_tool_rounds": 3,
        "thinking": {"type": "disabled"},
    }
    with TestClient(app) as client:
        response = client.post("/api/v1/chat/tool-calls", json=payload)
    assert response.status_code == 200, _scrub(response.text)
    body = response.json()
    content = body["choices"][0]["message"]["content"]
    assert body["tool_rounds"] >= 1, _scrub(content)
    assert "437" in content
    assert body["finish_reason"] == "stop"
    _assert_no_secret(response.text, live_logs.text)


def test_live_short_timeout_is_safe(live_logs: pytest.LogCaptureFixture) -> None:
    app = create_app(settings=_settings(deepseek_timeout_seconds=0.001))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "ping"}],
                "prompt_name": "default",
                "thinking": {"type": "disabled"},
            },
        )
    assert response.status_code in {502, 504}, _scrub(response.text)
    code = response.json()["error"]["code"]
    assert code in {"UPSTREAM_TIMEOUT", "UPSTREAM_ERROR"}
    assert "Traceback" not in response.text
    _assert_no_secret(response.text, live_logs.text)


def _wait_agent(client: TestClient, task_id: str, *, seconds: float = 35) -> dict[str, object]:
    deadline = time.monotonic() + seconds
    last: dict[str, object] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/agents/runs/{task_id}")
        assert response.status_code == 200, _scrub(response.text)
        last = response.json()
        if last["status"] in {"completed", "failed", "cancelled", "limit_reached"}:
            return last
        time.sleep(0.25)
    raise AssertionError(_scrub(str(last)))


def test_live_agent_tool_round_trip(live_logs: pytest.LogCaptureFixture) -> None:
    app = create_app(settings=_settings())
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/agents/runs",
            json={
                "message": "请调用 calculator 计算 23 * 19。不要心算，最终答案必须使用工具返回的数字。",
                "prompt_name": "calculator",
                "allowed_tools": ["calculator"],
            },
        )
        assert created.status_code == 200, _scrub(created.text)
        task_id = created.json()["task_id"]
        finished = _wait_agent(client, task_id)
    assert finished["status"] == "completed", _scrub(str(finished))
    assert finished["tool_calls"] >= 1
    assert "437" in str(finished["answer"])
    assert finished["error_code"] is None
    _assert_no_secret(str(finished), live_logs.text)


def test_live_agent_deadline_stops_the_run(live_logs: pytest.LogCaptureFixture) -> None:
    app = create_app(settings=_settings())
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/agents/runs",
            json={
                "message": "只回复一个词：pong",
                "prompt_name": "default",
                "allowed_tools": [],
                "policy": {"max_wall_time_seconds": 0.05},
            },
        )
        assert created.status_code == 200, _scrub(created.text)
        finished = _wait_agent(client, created.json()["task_id"], seconds=10)
    assert finished["status"] == "limit_reached", _scrub(str(finished))
    assert finished["error_code"] == "AGENT_DEADLINE_EXCEEDED"
    assert finished["answer"] is None
    _assert_no_secret(str(finished), live_logs.text)
