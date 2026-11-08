"""假 OAuth 和假远程 MCP。默认不访问外网。"""

from __future__ import annotations

import hashlib
from base64 import urlsafe_b64encode
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import Route

from app.core.exceptions import LoginRequiredError
from app.main import create_app
from app.mcp.catalog import Connector, load_catalog
from app.mcp.crypto import TokenCipher
from app.mcp.service import McpService
from app.mcp.store import MemoryStore
from app.models.mixins import utcnow
from app.providers.base import StreamChunk, UsageStats
from tests.conftest import make_settings
from tests.fakes import FakeProvider, chat_payload


def _fake_app() -> Starlette:
    world: dict[str, object] = {"codes": {}, "issued": {}, "queues": {}, "origin": "http://mcp.test"}

    async def protected(request: Request) -> JSONResponse:
        world["protected_path"] = request.url.path
        servers = world.get("authorization_servers") or [world["origin"]]
        return JSONResponse({"authorization_servers": servers})

    async def authorization(_: Request) -> JSONResponse:
        origin = str(world["origin"])
        return JSONResponse(
            {
                "issuer": origin,
                "authorization_endpoint": f"{origin}/authorize",
                "token_endpoint": str(world.get("token_endpoint") or f"{origin}/token"),
                "registration_endpoint": f"{origin}/register",
                "authorization_response_iss_parameter_supported": bool(world.get("iss_required")),
            }
        )

    async def register(_: Request) -> JSONResponse:
        return JSONResponse({"client_id": "cid-demo"})

    async def authorize(request: Request) -> RedirectResponse:
        query = parse_qs(request.url.query)
        code = "code-" + query["state"][0][:6]
        world["codes"][code] = query["code_challenge"][0]
        redirect = query["redirect_uri"][0]
        state = query["state"][0]
        location = f"{redirect}?code={code}&state={state}"
        if "return_iss" in world:
            location = f"{location}&iss={world['return_iss']}"
        return RedirectResponse(location, status_code=302)

    async def token(request: Request) -> JSONResponse:
        world["token_hits"] = int(world.get("token_hits") or 0) + 1
        if world.get("redirect_token"):
            return RedirectResponse(str(world["redirect_token"]), status_code=302)
        form = await request.form()
        if form.get("grant_type") == "refresh_token":
            current = world["issued"].get(form.get("refresh_token"))
            if not isinstance(current, str):
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            nxt = current + "-next"
            world["issued"][form.get("refresh_token")] = nxt
            return JSONResponse({"access_token": nxt, "refresh_token": form.get("refresh_token"), "expires_in": 3600})
        code = str(form.get("code") or "")
        verifier = str(form.get("code_verifier") or "")
        digest = hashlib.sha256(verifier.encode("utf-8")).digest()
        challenge = urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        if world["codes"].get(code) != challenge:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        access = "access-" + code[-6:]
        refresh = "refresh-" + code[-6:]
        world["issued"][refresh] = access
        return JSONResponse({"access_token": access, "refresh_token": refresh, "expires_in": 3600})

    def _answer(body: dict, request: Request) -> Response:
        world["last_bearer"] = request.headers.get("authorization", "")
        world["seen_cookie"] = request.headers.get("cookie", "")
        method = body.get("method")
        message_id = body.get("id")
        if method == "notifications/initialized":
            return Response(status_code=202)
        if method == "initialize":
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {"protocolVersion": "2024-11-05", "capabilities": {}, "serverInfo": {"name": "fake", "version": "1"}},
                }
            )
        if method == "tools/list":
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {
                        "tools": [
                            {
                                "name": "echo",
                                "description": "回传\n文本",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"message": {"type": "string", "description": "远端说明"}},
                                    "required": ["message"],
                                },
                            },
                            {"name": "delete_page", "description": "删除页面"},
                        ]
                    },
                }
            )
        if method == "tools/call":
            arguments = body.get("params", {}).get("arguments", {})
            text = arguments.get("message", "")
            response = JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {"content": [{"type": "text", "text": text}], "isError": False},
                }
            )
            cookie = world.get("set_cookie")
            if isinstance(cookie, str) and cookie:
                response.headers["set-cookie"] = cookie
                world["set_cookie"] = ""
            return response
        return JSONResponse({"jsonrpc": "2.0", "id": message_id, "error": {"code": -32601, "message": "no"}})

    async def mcp(request: Request) -> Response:
        if request.method == "GET":
            origin = str(world["origin"])
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": f'Bearer resource_metadata="{origin}/meta"'},
            )
        return _answer(await request.json(), request)

    async def sse(request: Request) -> StreamingResponse:
        queue: list[str] = []
        signal: dict[str, object] = {}

        async def wait_item() -> str:
            import asyncio

            while not queue:
                await asyncio.sleep(0.01)
            return queue.pop(0)

        world["sse_queue"] = queue

        async def generate():
            yield "event: endpoint\ndata: /messages?session=s1\n\n"
            while True:
                item = await wait_item()
                yield f"event: message\ndata: {item}\n\n"

        del signal
        return StreamingResponse(generate(), media_type="text/event-stream")

    async def messages(request: Request) -> Response:
        body = await request.json()
        response = _answer(body, request)
        payload = response.body.decode("utf-8") if response.body else ""
        if payload:
            world["sse_queue"].append(payload)
        return Response(status_code=202)

    app = Starlette(
        routes=[
            Route("/.well-known/oauth-protected-resource", protected),
            Route("/meta", protected),
            Route("/.well-known/oauth-authorization-server", authorization),
            Route("/register", register, methods=["POST"]),
            Route("/authorize", authorize),
            Route("/token", token, methods=["POST"]),
            Route("/mcp", mcp, methods=["GET", "POST"]),
            Route("/sse", sse),
            Route("/messages", messages, methods=["POST"]),
        ]
    )
    app.state.world = world
    return app


def _service(client: httpx.AsyncClient, catalog: tuple[Connector, ...], users: dict[str, str]) -> McpService:
    settings = make_settings(
        mcp_token_key="test-token-key-012345",
        mcp_allow_insecure=True,
        mcp_public_base_url="http://app.test",
        mcp_frontend_base_url="http://front.test",
        mcp_timeout_seconds=5,
    )
    return McpService(settings, MemoryStore(), client, catalog, users)


def _catalog(url: str, transport: str = "streamable_http") -> tuple[Connector, ...]:
    return (Connector("demo", "示例", transport, url, False),)


async def _authorize(service: McpService, client: httpx.AsyncClient, user_token: str) -> None:
    url = await service.begin(service.users[user_token], "demo")
    response = await client.get(url, follow_redirects=False)
    query = parse_qs(urlparse(response.headers["location"]).query)
    await service.finish(query["code"][0], query["state"][0])


def test_cipher_hides_plaintext_and_catalog_rejects_http() -> None:
    cipher = TokenCipher("test-token-key-012345")
    sealed = cipher.encrypt("access-1")
    assert "access-1" not in sealed
    assert cipher.decrypt(sealed) == "access-1"
    loaded = load_catalog(allow_insecure=False)
    assert all(item.url.startswith("https://") for item in loaded)


@pytest.mark.asyncio
async def test_oauth_refresh_selection_and_user_isolation() -> None:
    fake = _fake_app()
    transport = httpx.ASGITransport(app=fake)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test") as client:
        users = {"token-a": "user-a", "token-b": "user-b"}
        service = _service(client, _catalog("http://mcp.test/mcp"), users)
        await _authorize(service, client, "token-a")
        assert fake.state.world["protected_path"] == "/meta"
        await _authorize(service, client, "token-b")
        saved = await service.store.get_connection("user-a", "demo")
        assert saved is not None
        assert "access-" not in saved.access_token_encrypted
        saved.expires_at = utcnow() - timedelta(minutes=1)
        await service.store.save_connection(saved)
        chosen = await service.select("user-a", "demo", ["echo", "delete_page", "not-listed"])
        assert chosen == ["echo"]
        assert str(fake.state.world["last_bearer"]).endswith("-next")
        specs_a = await service.tool_specs("user-a")
        specs_b = await service.tool_specs("user-b")
        assert [item.name for item in specs_a] == ["mcp__demo__echo"]
        assert specs_b == []
        result = await specs_a[0].handler(specs_a[0].args_model(message="你好"))
        assert "你好" in result
        assert "untrusted" in result
        bearer = str(fake.state.world["last_bearer"]).removeprefix("Bearer ")
        assert bearer not in repr(service.store.connections)
        other = service.store.connections[("user-b", "demo")]
        assert bearer not in other.access_token_encrypted


@pytest.mark.asyncio
async def test_sse_transport_calls_the_selected_tool() -> None:
    import asyncio
    import socket

    import uvicorn

    fake = _fake_app()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    fake.state.world["origin"] = origin
    server = uvicorn.Server(
        uvicorn.Config(
            fake,
            host="127.0.0.1",
            port=port,
            log_level="error",
            lifespan="off",
            timeout_graceful_shutdown=1,
        )
    )
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("fake mcp server did not start")
        async with httpx.AsyncClient(base_url=origin, timeout=5) as client:
            service = _service(client, _catalog(f"{origin}/sse", "sse"), {"token-a": "user-a"})
            await _authorize(service, client, "token-a")
            await service.select("user-a", "demo", ["echo"])
            specs = await service.tool_specs("user-a")
            result = await specs[0].handler(specs[0].args_model(message="sse-ok"))
            assert "sse-ok" in result
    finally:
        for connection in list(server.server_state.connections):
            connection.transport.close()
        server.should_exit = True
        await task


def test_connector_api_and_chat_stay_with_the_current_user(provider: FakeProvider) -> None:
    fake = _fake_app()
    transport = httpx.ASGITransport(app=fake)
    client = httpx.AsyncClient(transport=transport, base_url="http://mcp.test")
    service = _service(client, _catalog("http://mcp.test/mcp"), {"token-a": "user-a", "token-b": "user-b"})
    app = create_app(settings=service.settings, provider=provider)
    app.state.mcp_service = service

    async def ready() -> None:
        await _authorize(service, client, "token-a")
        await service.select("user-a", "demo", ["echo"])

    import asyncio

    asyncio.run(ready())
    with TestClient(app) as test_client:
        missing = test_client.post("/api/v1/mcp/connectors/demo/connect")
        assert missing.status_code == 401
        listed = test_client.get("/api/v1/mcp/connectors", headers={"Authorization": "Bearer token-a"})
        assert listed.status_code == 200
        assert listed.json()["connectors"][0]["selected_tools"] == ["echo"]
        callback = test_client.get("/api/v1/mcp/oauth/callback", params={"code": "nope", "state": "nope"}, follow_redirects=False)
        assert callback.headers.get("location", "").endswith("/apps?connected=failed")
        assert "access-" not in callback.headers.get("location", "")
        assert "access-" not in callback.text
        provider.push(
            [
                StreamChunk(
                    id="s1",
                    model="deepseek-flash",
                    finish_reason="tool_calls",
                    tool_call_parts=[(0, "call_echo", "mcp__demo__echo", '{"message":"hello"}')],
                    usage=UsageStats(1, 1, 2),
                )
            ]
        )
        provider.push([StreamChunk(content="done", id="s2", model="deepseek-flash", finish_reason="stop", usage=UsageStats(1, 1, 2))])
        with test_client.stream(
            "POST",
            "/api/v1/chat/completions/stream",
            json=chat_payload(mode="agent"),
            headers={"Authorization": "Bearer token-a"},
        ) as response:
            assert response.status_code == 200
            response.read()
        offered = [item["function"]["name"] for item in provider.calls[0].tools]
        assert "mcp__demo__echo" in offered
        tool_message = next(item for item in provider.calls[1].messages if item["role"] == "tool")
        assert "hello" in tool_message["content"]
        assert "untrusted" in tool_message["content"]
        provider.push([StreamChunk(content="匿名", id="s3", model="deepseek-flash", finish_reason="stop", usage=UsageStats(1, 1, 2))])
        with test_client.stream(
            "POST",
            "/api/v1/chat/completions/stream",
            json=chat_payload(),
            headers={"Authorization": "Bearer token-b"},
        ) as response:
            assert response.status_code == 200
            response.read()
        later = provider.calls[-1].tools or []
        assert all(item["function"]["name"] != "mcp__demo__echo" for item in later)
    asyncio.run(client.aclose())


def test_connect_requires_a_known_user() -> None:
    service = _service(
        httpx.AsyncClient(),
        _catalog("https://example.com/mcp"),
        {},
    )

    class Dummy:
        headers: dict[str, str] = {}

    with pytest.raises(LoginRequiredError):
        import asyncio

        asyncio.run(service.require_user(Dummy()))
