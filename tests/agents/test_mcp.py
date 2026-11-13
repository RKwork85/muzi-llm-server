"""假 stdio 服务覆盖 MCP 握手、白名单和对话调用。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tests.support_agent_access import allow_legacy_agent_api


@pytest.fixture(autouse=True)
def _legacy_agent_api(monkeypatch) -> None:
    allow_legacy_agent_api(monkeypatch)

from app.agents.models import AgentRequest
from app.agents.toolsets import ask_tool_names
from app.core.exceptions import InvalidToolArgumentsError, ToolExecutionError, ToolNotAllowedError
from app.main import create_app
from app.providers.base import StreamChunk, UsageStats
from app.schemas.chat import ChatCompletionRequest
from app.services.tool_service import ToolService
from app.tools.mcp_client import McpSession, mcp_is_available, register_mcp_tools
from app.tools.registry import build_agent_registry
from tests.conftest import make_settings
from tests.fakes import FakeProvider, chat_payload

_SCRIPT = str(Path(__file__).with_name("mcp_fake_server.py"))
_SECRET = "sk-parent-should-not-leak"


def _settings(mode: str, **overrides: object):
    values: dict[str, object] = {
        "mcp_enabled": True,
        "mcp_server_name": "fake",
        "mcp_command": sys.executable,
        "mcp_args": f"{_SCRIPT},{mode}",
        "mcp_tool_allowlist": "echo",
        "mcp_timeout_seconds": 2,
    }
    values.update(overrides)
    return make_settings(**values)


def _chunk(content: str = "", **kwargs: object) -> StreamChunk:
    return StreamChunk(
        content=content,
        id="s1",
        model="deepseek-flash",
        usage=UsageStats(1, 1, 2),
        **kwargs,
    )


def test_mcp_is_off_by_default() -> None:
    settings = make_settings()
    assert settings.mcp_enabled is False
    assert settings.mcp_arg_list() == []
    assert settings.mcp_allowlist() == set()
    with pytest.raises(ValidationError):
        make_settings(mcp_enabled=True, mcp_server_name="bad name")


def test_request_cannot_carry_a_command() -> None:
    with pytest.raises(ValidationError):
        ChatCompletionRequest.model_validate(
            {"messages": [{"role": "user", "content": "hi"}], "mcp_command": sys.executable}
        )
    with pytest.raises(ValidationError):
        AgentRequest.model_validate(
            {"message": "hi", "prompt_name": "default", "mcp_command": sys.executable}
        )


def test_registration_keeps_only_safe_allowlisted_tools() -> None:
    registry = build_agent_registry()
    settings = _settings("ok", mcp_tools_network=True)
    register_mcp_tools(
        registry,
        McpSession(settings),
        [
            {
                "name": "echo",
                "description": "第一行\n第二行\x00" + ("长" * 300),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": "远端说明"},
                        "bad prop": {"type": "string"},
                    },
                    "required": ["message", "missing"],
                },
            },
            {"name": "secret", "description": "不要导入"},
            {"name": "bad name", "description": "名字不合法"},
        ],
        settings,
    )
    echo = registry.get("mcp__fake__echo")
    assert echo is not None
    assert registry.get("mcp__fake__secret") is None
    assert registry.get("mcp__fake__bad_name") is None
    assert "\n" not in echo.description
    assert "\x00" not in echo.description
    assert len(echo.description) <= 240
    assert echo.access == "read_network"
    assert echo.requires_network is True
    parameters = echo.openai_schema()["function"]["parameters"]
    assert parameters["properties"] == {"message": {"type": "string"}}
    assert parameters["required"] == ["message"]
    assert parameters["additionalProperties"] is False
    assert "mcp__fake__echo" not in ask_tool_names(registry, settings)


async def test_handshake_allowlist_env_and_reverse() -> None:
    previous = os.environ.get("DEEPSEEK_API_KEY")
    os.environ["DEEPSEEK_API_KEY"] = _SECRET
    session = McpSession(_settings("reverse"))
    try:
        tools = await session.start()
        names = {item.get("name") for item in tools}
        assert {"echo", "secret", "bad name"} <= names
        registry = build_agent_registry()
        register_mcp_tools(registry, session, tools, session._settings)
        assert registry.get("mcp__fake__secret") is None
        assert "mcp__fake__echo" not in ask_tool_names(registry, session._settings)
        from app.agents.toolsets import agent_chat_tool_names
        from app.tools.stdio_access import CURRENT_MCP_USER, stdio_access

        assert "mcp__fake__echo" not in agent_chat_tool_names(registry, session._settings)
        stdio_access.grant("user-a", {"mcp__fake__echo"})
        assert "mcp__fake__echo" not in stdio_access.names_for("user-b")
        service = ToolService(registry, session._settings)
        user_token = CURRENT_MCP_USER.set("user-a")
        try:
            messages = await service.execute(
                [_call("mcp__fake__echo", {"message": "__env__", "extra": _SECRET})]
            )
            content = messages[0]["content"]
            assert "clean" in content
            assert "untrusted" in content
            assert _SECRET not in content
            with pytest.raises(InvalidToolArgumentsError):
                await service.execute([_call("mcp__fake__echo", {})])
            with pytest.raises(ToolNotAllowedError):
                await service.execute([_call("mcp__fake__secret", {})])
        finally:
            CURRENT_MCP_USER.reset(user_token)
    finally:
        await session.aclose()
        if previous is None:
            os.environ.pop("DEEPSEEK_API_KEY", None)
        else:
            os.environ["DEEPSEEK_API_KEY"] = previous
        assert mcp_is_available() is False


async def test_framed_stdio_round_trip() -> None:
    session = McpSession(_settings("framed"))
    try:
        tools = await session.start()
        registry = build_agent_registry()
        register_mcp_tools(registry, session, tools, session._settings)
        from app.tools.stdio_access import CURRENT_MCP_USER, stdio_access

        stdio_access.grant("user-a", {"mcp__fake__echo"})
        service = ToolService(registry, session._settings)
        user_token = CURRENT_MCP_USER.set("user-a")
        try:
            messages = await service.execute([_call("mcp__fake__echo", {"message": "你好"})])
        finally:
            CURRENT_MCP_USER.reset(user_token)
        assert "你好" in messages[0]["content"]
    finally:
        await session.aclose()


async def test_unknown_process_and_timeout_do_not_restart() -> None:
    bad = McpSession(_settings("badjson", mcp_timeout_seconds=1))
    with pytest.raises(RuntimeError):
        await bad.start()
    await bad.aclose()
    assert mcp_is_available() is False

    missing = McpSession(_settings("ok", mcp_command=str(Path("C:/missing/mcp.exe"))))
    with pytest.raises(RuntimeError):
        await missing.start()
    assert mcp_is_available() is False

    settings = _settings("hang", mcp_timeout_seconds=0.2)
    session = McpSession(settings)
    try:
        tools = await session.start()
        registry = build_agent_registry()
        register_mcp_tools(registry, session, tools, settings)
        from app.tools.stdio_access import CURRENT_MCP_USER, stdio_access

        stdio_access.grant("user-a", {"mcp__fake__echo"})
        service = ToolService(registry, settings)
        user_token = CURRENT_MCP_USER.set("user-a")
        try:
            with pytest.raises(ToolExecutionError):
                await service.execute([_call("mcp__fake__echo", {"message": "hi"})])
        finally:
            CURRENT_MCP_USER.reset(user_token)
        process = session._process
        assert process is not None
        process.kill()
        await process.wait()
        user_token = CURRENT_MCP_USER.set("user-a")
        try:
            with pytest.raises(ToolExecutionError):
                await service.execute([_call("mcp__fake__echo", {"message": "again"})])
        finally:
            CURRENT_MCP_USER.reset(user_token)
        assert session._process is process
    finally:
        await session.aclose()


def test_failed_startup_still_serves_chat(provider: FakeProvider) -> None:
    provider.push([_chunk("你好", finish_reason="stop")])
    app = create_app(
        settings=_settings("crash", mcp_command=str(Path("C:/missing/mcp.exe"))),
        provider=provider,
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/chat/completions/stream", json=chat_payload())
    assert response.status_code == 200
    assert "你好" in response.text
    offered = provider.calls[0].tools or []
    assert all(not item["function"]["name"].startswith("mcp__") for item in offered)


def test_ask_stream_calls_allowlisted_tool(provider: FakeProvider) -> None:
    provider.push(
        [
            _chunk(
                finish_reason="tool_calls",
                tool_call_parts=[(0, "call_echo", "mcp__fake__echo", '{"message":"hello"}')],
            )
        ]
    )
    provider.push([_chunk("已收到", finish_reason="stop")])
    app = create_app(settings=_settings("ok"), provider=provider)
    with TestClient(app) as client:
        from app.mcp.service import McpService
        from app.mcp.store import MemoryStore

        import httpx

        app.state.mcp_service = McpService(
            app.state.settings,
            MemoryStore(),
            httpx.AsyncClient(),
            catalog=(),
            users={"token-a": "user-a"},
        )
        with client.stream(
            "POST",
            "/api/v1/chat/completions/stream",
            json=chat_payload(mode="agent"),
            headers={"Authorization": "Bearer token-a"},
        ) as response:
            assert response.status_code == 200
            body = response.read().decode("utf-8")
    offered = [item["function"]["name"] for item in provider.calls[0].tools]
    assert "mcp__fake__echo" in offered
    assert "mcp__fake__secret" not in offered
    tool_message = next(item for item in provider.calls[1].messages if item["role"] == "tool")
    payload = json.loads(tool_message["content"])
    assert "untrusted" in payload["result"]
    assert "hello" in payload["result"]
    assert "event: done" in body
    provider.push([_chunk("只回答", finish_reason="stop")])
    with client.stream("POST", "/api/v1/chat/completions/stream", json=chat_payload()) as response:
        assert response.status_code == 200
        response.read()
    ask_offered = [item["function"]["name"] for item in provider.calls[-1].tools]
    assert "mcp__fake__echo" not in ask_offered
    provider.push([_chunk("计划", finish_reason="stop")])
    with client.stream(
        "POST",
        "/api/v1/chat/completions/stream",
        json=chat_payload(mode="plan"),
    ) as response:
        assert response.status_code == 200
        response.read()
    plan_offered = [item["function"]["name"] for item in provider.calls[-1].tools]
    assert "write_text_file" not in plan_offered
    assert "mcp__fake__echo" not in plan_offered
    assert mcp_is_available() is False


def _call(name: str, arguments: dict[str, object]):
    from tests.fakes import make_tool_call

    return make_tool_call("call_mcp", name, json.dumps(arguments))
