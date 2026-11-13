"""Agent 创建与内存任务归属的 API 入口测试。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.authorization.application.authorization_service import AuthorizationService
from app.core.exceptions import PermissionDeniedError
from app.providers.base import StreamChunk, UsageStats
from app.services.auth_service import AuthService
from tests.fakes import FakeProvider, chat_payload, make_result


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def commit(self):
        return None


def _install_directory(monkeypatch, directory: dict[str, set[str]]) -> dict[str, SimpleNamespace]:
    users = {token: SimpleNamespace(id=uuid.uuid4(), status="active") for token in directory}

    async def user_for_token(_self, raw: str):
        return users.get(raw)

    async def require_permission(_self, user_id, permission, resource_id=None, context=None):
        del resource_id, context
        allowed = next((directory[token] for token, user in users.items() if user.id == user_id), set())
        if permission not in allowed:
            raise PermissionDeniedError()

    monkeypatch.setattr("app.api.agent_access.get_session_factory", lambda: (lambda: _Session()))
    monkeypatch.setattr(AuthService, "user_for_token", user_for_token)
    monkeypatch.setattr(AuthorizationService, "require_permission", require_permission)
    return users


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_agent_execute_allows_create_without_conversation(client: TestClient, provider: FakeProvider, monkeypatch) -> None:
    _install_directory(monkeypatch, {"token-executor-0001": {"agent.execute"}})
    provider.push(make_result("可以执行"))
    created = client.post(
        "/api/v1/agents/runs",
        headers=_headers("token-executor-0001"),
        json={"message": "只回答", "prompt_name": "default"},
    )
    assert created.status_code == 200
    body = created.json()
    assert set(body) >= {"task_id", "request_id", "status", "answer", "steps", "tool_calls", "error_code", "usage"}
    followed = client.get(f"/api/v1/agents/runs/{body['task_id']}", headers=_headers("token-executor-0001"))
    assert followed.status_code == 200


def test_missing_agent_execute_rejects_create_without_conversation(client: TestClient, provider: FakeProvider, monkeypatch) -> None:
    seen: list[str] = []

    users = _install_directory(monkeypatch, {"token-guest-00000001": set()})

    async def require_permission(_self, user_id, permission, resource_id=None, context=None):
        del resource_id, context, user_id
        seen.append(permission)
        if permission not in set():
            raise PermissionDeniedError()

    monkeypatch.setattr(AuthorizationService, "require_permission", require_permission)
    del users
    provider.push(make_result("不应执行"))
    rejected = client.post(
        "/api/v1/agents/runs",
        headers=_headers("token-guest-00000001"),
        json={"message": "只回答", "prompt_name": "default"},
    )
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "PERMISSION_DENIED"
    assert seen == ["agent.execute"]
    assert provider.calls == []


def test_other_user_cannot_read_cancel_or_subscribe_memory_task(client: TestClient, provider: FakeProvider, monkeypatch) -> None:
    _install_directory(
        monkeypatch,
        {
            "token-owner-00000001": {"agent.execute"},
            "token-other-00000001": {"agent.execute"},
        },
    )
    provider.push(make_result("只属于甲"))
    created = client.post(
        "/api/v1/agents/runs",
        headers=_headers("token-owner-00000001"),
        json={"message": "只回答", "prompt_name": "default"},
    )
    assert created.status_code == 200
    task_id = created.json()["task_id"]
    try:
        uuid.UUID(task_id)
        is_uuid = True
    except ValueError:
        is_uuid = False
    assert is_uuid is False
    owner_headers = _headers("token-owner-00000001")
    other_headers = _headers("token-other-00000001")
    assert client.get(f"/api/v1/agents/runs/{task_id}", headers=other_headers).status_code == 404
    assert client.post(f"/api/v1/agents/runs/{task_id}/cancel", headers=other_headers).status_code == 404
    with client.stream("GET", f"/api/v1/agents/runs/{task_id}/events", headers=other_headers) as response:
        assert response.status_code == 404
    assert client.get(f"/api/v1/agents/runs/{task_id}", headers=owner_headers).status_code == 200
    assert client.get(f"/api/v1/agents/runs/{task_id}").status_code == 401


def test_anonymous_and_database_down_cannot_create(client: TestClient, provider: FakeProvider, monkeypatch) -> None:
    provider.push(make_result("不应执行"))
    anonymous = client.post("/api/v1/agents/runs", json={"message": "只回答", "prompt_name": "default"})
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "LOGIN_REQUIRED"
    assert provider.calls == []

    def unavailable():
        raise RuntimeError("PostgreSQL 未初始化")

    monkeypatch.setattr("app.api.agent_access.get_session_factory", unavailable)
    denied = client.post(
        "/api/v1/agents/runs",
        headers=_headers("token-executor-0001"),
        json={"message": "只回答", "prompt_name": "default"},
    )
    assert denied.status_code == 503
    assert denied.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
    assert provider.calls == []


def test_chat_agent_mode_requires_execute_and_ask_stream_stays(client: TestClient, provider: FakeProvider, monkeypatch) -> None:
    _install_directory(monkeypatch, {"token-executor-0001": {"agent.execute"}, "token-guest-00000001": set()})
    blocked = client.stream(
        "POST",
        "/api/v1/chat/completions/stream",
        headers=_headers("token-guest-00000001"),
        json=chat_payload(mode="agent"),
    )
    with blocked as response:
        assert response.status_code == 403
        response.read()
    assert provider.calls == []

    provider.push(
        [
            StreamChunk(content="你", id="s1", model="deepseek-flash"),
            StreamChunk(content="好", id="s1", model="deepseek-flash", finish_reason="stop", usage=UsageStats(1, 2, 3)),
        ]
    )
    with client.stream("POST", "/api/v1/chat/completions/stream", json=chat_payload()) as response:
        assert response.status_code == 200
        body = response.read().decode("utf-8")
    assert [line[7:] for line in body.splitlines() if line.startswith("event: ")] == ["delta", "delta", "usage", "done"]
