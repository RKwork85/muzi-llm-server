"""R2.1 协议生命周期。假进程和进程内 ASGI，不访问真实 MCP 服务。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from app.mcp.http_client import RemoteHub
from app.mcp.protocol import (
    PREFERRED_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    ProtocolError,
    accept_initialize_result,
    client_initialize_params,
)
from app.mcp.store import ConnectionRecord
from app.tools.mcp_client import McpSession, register_mcp_tools
from app.tools.registry import build_agent_registry
from app.tools.stdio_access import stdio_access
from tests.conftest import make_settings
from tests.test_remote_mcp import _catalog, _service

_SCRIPT = str(Path(__file__).resolve().parent / "agents" / "mcp_fake_server.py")


def test_supported_versions_reject_stateless_and_lookalikes() -> None:
    params = client_initialize_params()
    assert params["protocolVersion"] == PREFERRED_PROTOCOL_VERSION
    assert params["capabilities"] == {}
    assert "2026-07-28" not in SUPPORTED_PROTOCOL_VERSIONS
    accepted = accept_initialize_result(
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": True}, "resources": {}},
            "serverInfo": {"name": "fake", "version": "1"},
        }
    )
    assert accepted.protocol_version == "2024-11-05"
    assert accepted.tools_list_changed is True
    for bad in ("2026-07-28", "2024-11-05 ", "2024-11-05.evil", "http://2024-11-05"):
        with pytest.raises(ProtocolError) as caught:
            accept_initialize_result({"protocolVersion": bad, "capabilities": {}, "serverInfo": {}})
        assert caught.value.reason == "protocol_version"


def _stdio_settings(mode: str, **overrides: object):
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


@pytest.mark.asyncio
async def test_stdio_negotiates_supported_version_and_answers_ping() -> None:
    session = McpSession(_stdio_settings("prefer"))
    try:
        await session.start()
        assert session.protocol_version == PREFERRED_PROTOCOL_VERSION
        assert session.alive is True
    finally:
        await session.aclose()

    ping = McpSession(_stdio_settings("ping"))
    try:
        tools = await ping.start()
        assert any(item.get("name") == "echo" for item in tools)
    finally:
        await ping.aclose()


@pytest.mark.asyncio
async def test_stdio_rejects_unsupported_version_without_staying_up() -> None:
    session = McpSession(_stdio_settings("bad-version"))
    with pytest.raises(ProtocolError) as caught:
        await session.start()
    assert caught.value.reason == "protocol_version"
    assert session.alive is False
    await session.aclose()


@pytest.mark.asyncio
async def test_stdio_pages_change_cancel_and_rpc_error() -> None:
    pages = McpSession(_stdio_settings("pages"))
    try:
        tools = await pages.start()
        assert [item.get("name") for item in tools] == ["marker", "echo"]
    finally:
        await pages.aclose()

    changed = McpSession(_stdio_settings("changed"))
    registry = build_agent_registry()
    try:
        listed = await changed.start()
        register_mcp_tools(registry, changed, listed, changed._settings)
        stdio_access.grant("user-a", {"mcp__fake__echo"})
        stdio_access.grant("user-b", set())
        for _ in range(20):
            if changed.tools_changed:
                break
            await asyncio.sleep(0.01)
        assert changed.tools_changed is True
        refreshed = await changed.refresh_tools()
        assert [item.get("name") for item in refreshed] == ["echo"]
        assert stdio_access.allows("user-a", "mcp__fake__echo")
        assert not stdio_access.allows("user-b", "mcp__fake__echo")
        assert changed.tools_changed is False
    finally:
        await changed.aclose()

    capture = Path(__file__).resolve().parent / "_mcp_cancel_capture.json"
    if capture.exists():
        capture.unlink()
    cancel = McpSession(
        _stdio_settings("cancel", mcp_args=f"{_SCRIPT},cancel,{capture}", mcp_timeout_seconds=0.2)
    )
    try:
        await cancel.start()
        with pytest.raises(TimeoutError):
            await cancel.call_tool("echo", {"message": "slow"})
        for _ in range(30):
            if capture.exists():
                break
            await asyncio.sleep(0.05)
        payload = json.loads(capture.read_text(encoding="utf-8"))
        assert payload["method"] == "notifications/cancelled"
        assert payload["params"]["reason"] == "timeout"
        assert isinstance(payload["params"]["requestId"], int)
        assert cancel._process is not None
    finally:
        await cancel.aclose()
        if capture.exists():
            capture.unlink()

    broken = McpSession(_stdio_settings("ok"))
    try:
        await broken.start()
        with pytest.raises(ProtocolError) as caught:
            await broken.call_tool("echo", {"message": "bad"})
        assert caught.value.reason == "invalid_params"
        assert broken.alive is True
        assert await broken.call_tool("echo", {"message": "still"}) == "still"
    finally:
        await broken.aclose()


def _http_app() -> Starlette:
    world: dict[str, object] = {
        "version": "2024-11-05",
        "versions": [],
        "sessions": [],
        "expire": False,
        "sse": False,
        "pages": False,
        "cancelled": None,
        "deleted": None,
    }

    async def mcp(request: Request) -> Response:
        if request.method == "DELETE":
            world["deleted"] = request.headers.get("mcp-session-id")
            return Response(status_code=204)
        body = await request.json()
        versions = world["versions"]
        assert isinstance(versions, list)
        versions.append(request.headers.get("mcp-protocol-version"))
        sessions = world["sessions"]
        assert isinstance(sessions, list)
        sessions.append(request.headers.get("mcp-session-id"))
        method = body.get("method")
        session_id = request.headers.get("mcp-session-id")
        if world["expire"] is True and session_id == "abc" and method != "initialize":
            return Response(status_code=404)
        if method in {"notifications/initialized", "notifications/cancelled"}:
            if method == "notifications/cancelled":
                world["cancelled"] = body
            return Response(status_code=202)
        if method == "initialize":
            session = "def" if world["expire"] is True else "abc"
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": body.get("id"),
                    "result": {
                        "protocolVersion": world["version"],
                        "capabilities": {"tools": {"listChanged": True}},
                        "serverInfo": {"name": "fake", "version": "1"},
                    },
                },
                headers={"Mcp-Session-Id": session},
            )
        if method == "tools/list":
            params = body.get("params") if isinstance(body.get("params"), dict) else {}
            cursor = params.get("cursor")
            if world["pages"] is True and cursor != "p2":
                result = {"tools": [{"name": "marker", "inputSchema": {"type": "object"}}], "nextCursor": "p2"}
            elif world["pages"] is True:
                result = {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}
            else:
                result = {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}
            payload = {"jsonrpc": "2.0", "id": body.get("id"), "result": result}
            if world["sse"] is True:
                note = json.dumps({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
                data = json.dumps(payload)
                body_text = f"event: message\ndata: {note}\n\nevent: message\ndata: {data}\n\n"
                return Response(body_text, media_type="text/event-stream")
            return JSONResponse(payload)
        if method == "tools/call":
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": body.get("id"),
                    "result": {"content": [{"type": "text", "text": "ok"}], "isError": False},
                }
            )
        return JSONResponse(
            {"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": -32601, "message": "Method not found"}}
        )

    app = Starlette(routes=[Route("/mcp", mcp, methods=["GET", "POST", "DELETE"])])
    app.state.world = world
    return app


@pytest.mark.asyncio
async def test_http_negotiates_pages_session_and_cancel() -> None:
    app = _http_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test", timeout=2) as client:
        hub = RemoteHub(client)
        listed = await hub.call(
            "user-a",
            "demo",
            "streamable_http",
            "http://mcp.test/mcp",
            "token-a",
            "tools/list",
            {},
        )
        assert listed["result"]["tools"][0]["name"] == "echo"
        versions = app.state.world["versions"]
        assert versions[0] == PREFERRED_PROTOCOL_VERSION
        assert versions[-1] == "2024-11-05"
        session = hub._sessions[("user-a", "demo")]
        assert session.protocol == "2024-11-05"
        assert session.session_id == "abc"

        app.state.world["version"] = "2026-07-28"
        with pytest.raises(ProtocolError) as caught:
            await hub.call("user-b", "demo", "streamable_http", "http://mcp.test/mcp", "token-b", "tools/list", {})
        assert caught.value.reason == "protocol_version"
        assert ("user-b", "demo") not in hub._sessions

        app.state.world["version"] = "2024-11-05"
        app.state.world["expire"] = True
        again = await hub.call(
            "user-a",
            "demo",
            "streamable_http",
            "http://mcp.test/mcp",
            "token-a",
            "tools/list",
            {},
        )
        assert again["result"]["tools"][0]["name"] == "echo"
        assert hub._sessions[("user-a", "demo")].session_id == "def"

        app.state.world["sse"] = True
        app.state.world["expire"] = False
        streamed = await hub.call(
            "user-c",
            "demo",
            "streamable_http",
            "http://mcp.test/mcp",
            "token-c",
            "tools/list",
            {},
        )
        assert streamed["result"]["tools"][0]["name"] == "echo"
        assert hub._sessions[("user-c", "demo")].tools_changed is True

        seen: list[dict[str, object]] = []

        def scripted(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8"))
            seen.append(body)
            method = body.get("method")
            if method == "tools/call":
                raise httpx.ReadTimeout("slow", request=request)
            if method == "notifications/cancelled":
                return httpx.Response(202)
            if method == "initialize":
                return httpx.Response(
                    200,
                    headers={"mcp-session-id": "slow"},
                    json={
                        "jsonrpc": "2.0",
                        "id": body.get("id"),
                        "result": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "serverInfo": {"name": "fake", "version": "1"},
                        },
                    },
                )
            return httpx.Response(202)

        slow = httpx.AsyncClient(transport=httpx.MockTransport(scripted), base_url="http://mcp.test")
        try:
            slow_hub = RemoteHub(slow)
            with pytest.raises(ProtocolError) as timed:
                await slow_hub.call(
                    "user-d",
                    "demo",
                    "streamable_http",
                    "http://mcp.test/mcp",
                    "token-d",
                    "tools/call",
                    {"name": "echo", "arguments": {}},
                )
            assert timed.value.reason == "timeout"
            assert any(item.get("method") == "notifications/cancelled" for item in seen)
            assert ("user-d", "demo") in slow_hub._sessions
        finally:
            await slow.aclose()


@pytest.mark.asyncio
async def test_remote_tool_list_follows_cursor_and_survives_rpc_error() -> None:
    app = _http_app()
    app.state.world["pages"] = True
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test", timeout=2) as client:
        service = _service(client, _catalog("http://mcp.test/mcp"), {"token-a": "user-a"})
        await service.store.save_connection(
            ConnectionRecord(
                user_id="user-a",
                connector_name="demo",
                status="connected",
                access_token_encrypted=service.cipher().encrypt("access-demo"),
                refresh_token_encrypted=None,
                expires_at=None,
            )
        )
        connector = service.connector("demo")
        tools = await service._list("user-a", connector)
        assert [item["name"] for item in tools] == ["marker", "echo"]
        with pytest.raises(ProtocolError) as caught:
            await service._rpc("user-a", connector, "unknown/method", {})
        assert caught.value.reason == "method_not_found"
        again = await service._list("user-a", connector)
        assert [item["name"] for item in again] == ["marker", "echo"]
