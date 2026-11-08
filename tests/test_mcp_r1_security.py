"""R1 安全回归。假服务器，不使用真实令牌。"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import sys
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agents.toolsets import agent_tool_names
from app.core.exceptions import LoginRequiredError, ToolExecutionError, ToolNotAllowedError
from app.main import create_app
from app.mcp.oauth_guard import (
    OAuthFlowError,
    addresses_allowed,
    issuers_equal,
    screen_endpoint,
    trusted_issuer,
)
from app.mcp.pool import ClientPool, _PinningBackend
from app.mcp.service import McpService
from app.mcp.store import MemoryStore
from app.models.mixins import utcnow
from app.providers.base import StreamChunk, UsageStats
from app.services.tool_service import ToolService
from app.tools.mcp_client import McpSession, register_mcp_tools
from app.tools.registry import build_agent_registry
from app.tools.stdio_access import CURRENT_MCP_USER, stdio_access
from tests.conftest import make_settings
from tests.fakes import FakeProvider, chat_payload, make_tool_call
from tests.test_remote_mcp import _authorize, _catalog, _fake_app, _service


def test_issuer_match_rejects_lookalikes() -> None:
    anchor = trusted_issuer("https://mcp.test/mcp", "")
    assert anchor == "https://mcp.test"
    assert issuers_equal(anchor, "https://mcp.test")
    assert not issuers_equal(anchor, "https://mcp.test.evil")
    assert not issuers_equal(anchor, "https://evil-mcp.test")
    assert not issuers_equal(anchor, "https://mcp.test/extra")
    assert not issuers_equal(anchor, "https://mcp.test/")
    assert not issuers_equal(anchor, "https://user:pass@mcp.test")
    assert not issuers_equal(anchor, "http://mcp.test")


def test_endpoint_policy_blocks_unsafe_targets() -> None:
    private = ipaddress.ip_address("10.1.1.1")
    loopback = ipaddress.ip_address("127.0.0.1")
    public = ipaddress.ip_address("8.8.8.8")
    metadata = ipaddress.ip_address("169.254.169.254")
    shared = ipaddress.ip_address("100.64.0.1")
    assert not addresses_allowed([private], allow_insecure=True)
    assert not addresses_allowed([metadata], allow_insecure=True)
    assert not addresses_allowed([shared], allow_insecure=True)
    assert not addresses_allowed([loopback], allow_insecure=False)
    assert addresses_allowed([loopback], allow_insecure=True)
    assert addresses_allowed([public], allow_insecure=False)

    async def check(url: str, *, insecure: bool, resolver=None) -> str:
        return await screen_endpoint(
            url,
            allow_insecure=insecure,
            resolver=resolver,
            allow_unresolved_name=False,
        )

    with pytest.raises(OAuthFlowError):
        asyncio.run(check("http://mcp.test/token", insecure=False))
    with pytest.raises(OAuthFlowError):
        asyncio.run(check("https://user:pw@mcp.test/token", insecure=False))
    with pytest.raises(OAuthFlowError):
        asyncio.run(check("https://10.1.1.1/token", insecure=False))
    with pytest.raises(OAuthFlowError):
        asyncio.run(check("https://127.0.0.1/token", insecure=False))
    with pytest.raises(OAuthFlowError):
        asyncio.run(check("file:///tmp/token", insecure=True))

    async def private_dns(host: str):
        del host
        return [ipaddress.ip_address("10.9.9.9")]

    with pytest.raises(OAuthFlowError):
        asyncio.run(check("https://mcp.test/token", insecure=False, resolver=private_dns))
    assert asyncio.run(check("http://127.0.0.1/token", insecure=True)) == "http://127.0.0.1/token"


@pytest.mark.asyncio
async def test_malicious_metadata_does_not_receive_tokens() -> None:
    fake = _fake_app()
    transport = httpx.ASGITransport(app=fake)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test") as client:
        service = _service(client, _catalog("http://mcp.test/mcp"), {"token-a": "user-a"})
        fake.state.world["authorization_servers"] = ["http://mcp.test.evil"]
        with pytest.raises(LoginRequiredError):
            await service.begin("user-a", "demo")
        assert int(fake.state.world.get("token_hits") or 0) == 0

        fake.state.world["authorization_servers"] = ["http://mcp.test"]
        fake.state.world["token_endpoint"] = "http://10.1.1.1/token"
        with pytest.raises(LoginRequiredError):
            await service.begin("user-a", "demo")
        assert int(fake.state.world.get("token_hits") or 0) == 0

        fake.state.world["token_endpoint"] = "http://mcp.test/token"
        fake.state.world["iss_required"] = True
        fake.state.world["return_iss"] = "http://evil.test"
        url = await service.begin("user-a", "demo")
        response = await client.get(url, follow_redirects=False)
        query = parse_qs(urlparse(response.headers["location"]).query)
        before = int(fake.state.world.get("token_hits") or 0)
        with pytest.raises(LoginRequiredError):
            await service.finish(query["code"][0], query["state"][0], query["iss"][0])
        assert int(fake.state.world.get("token_hits") or 0) == before
        await service.aclose()


@pytest.mark.asyncio
async def test_redirect_and_refresh_stay_on_the_bound_endpoint() -> None:
    fake = _fake_app()
    transport = httpx.ASGITransport(app=fake)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test") as client:
        service = _service(client, _catalog("http://mcp.test/mcp"), {"token-a": "user-a", "token-b": "user-b"})
        await _authorize(service, client, "token-a")
        await _authorize(service, client, "token-b")
        saved = await service.store.get_connection("user-a", "demo")
        assert saved is not None
        saved.expires_at = utcnow() - timedelta(minutes=1)
        await service.store.save_connection(saved)
        fake.state.world["token_endpoint"] = "http://evil.test/steal"
        chosen = await service.select("user-a", "demo", ["echo"])
        assert chosen == ["echo"]
        other = await service.store.get_connection("user-b", "demo")
        assert other is not None
        assert other.access_token_encrypted != saved.access_token_encrypted

        fresh = _service(client, _catalog("http://mcp.test/mcp"), {"token-a": "user-a"})
        fake.state.world["token_endpoint"] = "http://mcp.test/token"
        fake.state.world["redirect_token"] = "http://evil.test/steal"
        url = await fresh.begin("user-a", "demo")
        response = await client.get(url, follow_redirects=False)
        query = parse_qs(urlparse(response.headers["location"]).query)
        before = int(fake.state.world.get("token_hits") or 0)
        with pytest.raises(LoginRequiredError):
            await fresh.finish(query["code"][0], query["state"][0], query.get("iss", [""])[0])
        assert int(fake.state.world.get("token_hits") or 0) == before + 1
        await service.aclose()
        await fresh.aclose()


@pytest.mark.asyncio
async def test_cookie_jars_do_not_cross_users() -> None:
    fake = _fake_app()
    transport = httpx.ASGITransport(app=fake)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test") as client:
        users = {"token-a": "user-a", "token-b": "user-b"}
        service = _service(client, _catalog("http://mcp.test/mcp"), users)
        await _authorize(service, client, "token-a")
        await _authorize(service, client, "token-b")
        await service.select("user-a", "demo", ["echo"])
        await service.select("user-b", "demo", ["echo"])
        specs_a = await service.tool_specs("user-a")
        specs_b = await service.tool_specs("user-b")
        fake.state.world["set_cookie"] = "tenant=a; Path=/"
        await specs_a[0].handler(specs_a[0].args_model(message="a"))
        fake.state.world["seen_cookie"] = ""
        await specs_b[0].handler(specs_b[0].args_model(message="b"))
        assert "tenant=a" not in str(fake.state.world.get("seen_cookie", ""))
        fake.state.world["set_cookie"] = "tenant=b; Path=/"
        await specs_b[0].handler(specs_b[0].args_model(message="b2"))
        await specs_a[0].handler(specs_a[0].args_model(message="a2"))
        assert "tenant=b" not in str(fake.state.world.get("seen_cookie", ""))
        client_a = service.pool.peek("user-a", "demo")
        client_b = service.pool.peek("user-b", "demo")
        assert client_a is not None and client_b is not None and client_a is not client_b
        await service.disconnect("user-a", "demo")
        assert service.pool.peek("user-a", "demo") is None
        assert service.pool.peek("user-b", "demo") is not None
        await service.aclose()


@pytest.mark.asyncio
async def test_pinning_backend_connects_only_to_checked_address(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    class Inner:
        async def connect_tcp(self, host: str, port: int, timeout=None, local_address=None, socket_options=None):
            del port, timeout, local_address, socket_options
            seen["host"] = host
            return "stream"

        async def sleep(self, seconds: float) -> None:
            del seconds

    async def public(host: str):
        del host
        return [ipaddress.ip_address("8.8.8.8")]

    monkeypatch.setattr("app.mcp.pool.system_resolver", public)
    backend = _PinningBackend(Inner(), allow_insecure=False)
    assert await backend.connect_tcp("mcp.test", 443) == "stream"
    assert seen["host"] == "8.8.8.8"

    async def private(host: str):
        del host
        return [ipaddress.ip_address("10.9.9.9")]

    monkeypatch.setattr("app.mcp.pool.system_resolver", private)
    with pytest.raises(OAuthFlowError):
        await backend.connect_tcp("mcp.test", 443)


def test_pool_clients_are_not_reused_across_users() -> None:
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(cookies=httpx.Cookies())

    pool = ClientPool(factory)
    first = pool.get("user-a", "demo")
    second = pool.get("user-b", "demo")
    again = pool.get("user-a", "demo")
    assert first is again
    assert first is not second
    first.cookies.set("tenant", "a")
    assert "tenant" not in str(second.cookies)
    asyncio.run(pool.discard("user-a", "demo"))
    assert pool.peek("user-a", "demo") is None
    assert first.is_closed
    assert not second.is_closed
    asyncio.run(pool.aclose())


def _stdio_settings():
    script = str(Path(__file__).resolve().parent / "agents" / "mcp_fake_server.py")
    return make_settings(
        mcp_enabled=True,
        mcp_server_name="fake",
        mcp_command=sys.executable,
        mcp_args=f"{script},ok",
        mcp_tool_allowlist="echo",
        mcp_timeout_seconds=2,
    )


def test_stdio_grants_do_not_cross_users() -> None:
    settings = _stdio_settings()

    async def scenario() -> None:
        session = McpSession(settings)
        tools = await session.start()
        registry = build_agent_registry()
        register_mcp_tools(registry, session, tools, settings)
        stdio_access.grant("user-a", {"mcp__fake__echo"})
        stdio_access.grant("user-b", set())
        assert stdio_access.names_for("user-a") == {"mcp__fake__echo"}
        assert stdio_access.names_for("user-b") == set()
        assert agent_tool_names(registry, settings, ["mcp"], None, "user-b") == ()
        assert "mcp__fake__echo" in agent_tool_names(registry, settings, ["mcp"], None, "user-a")
        service = ToolService(registry, settings)

        async def call_as(user: str, name: str) -> str:
            token = CURRENT_MCP_USER.set(user)
            try:
                messages = await service.execute(
                    [make_tool_call("c1", name, json.dumps({"message": user}))]
                )
            finally:
                CURRENT_MCP_USER.reset(token)
            return messages[0]["content"]

        assert "user-a" in await call_as("user-a", "mcp__fake__echo")
        with pytest.raises(ToolExecutionError):
            await call_as("user-b", "mcp__fake__echo")
        with pytest.raises(ToolNotAllowedError):
            await call_as("user-a", "mcp__fake__not_real")
        left, right = await asyncio.gather(
            call_as("user-a", "mcp__fake__echo"),
            call_as("user-a", "mcp__fake__echo"),
        )
        assert "user-a" in left and "user-a" in right
        stdio_access.revoke("user-a")
        with pytest.raises(ToolExecutionError):
            await call_as("user-a", "mcp__fake__echo")
        process = session._process
        assert process is not None
        process.kill()
        await process.wait()
        stdio_access.grant("user-a", {"mcp__fake__echo"})
        with pytest.raises(ToolExecutionError):
            await call_as("user-a", "mcp__fake__echo")
        await session.aclose()
        restarted = McpSession(settings)
        try:
            again = await restarted.start()
            register_mcp_tools(registry, restarted, again, settings)
            assert not stdio_access.allows("user-a", "mcp__fake__echo")
            assert not stdio_access.allows("user-b", "mcp__fake__echo")
            stdio_access.grant("user-a", {"mcp__fake__echo"})
            assert stdio_access.allows("user-a", "mcp__fake__echo")
            assert not stdio_access.allows("user-b", "mcp__fake__echo")
        finally:
            await restarted.aclose()

    asyncio.run(scenario())


def test_agent_stream_hides_stdio_from_other_users(provider: FakeProvider) -> None:
    settings = _stdio_settings()
    app = create_app(settings=settings, provider=provider)
    service: McpService | None = None
    with TestClient(app) as client:
        service = McpService(
            settings,
            MemoryStore(),
            httpx.AsyncClient(),
            catalog=(),
            users={"token-a": "user-a", "token-b": "user-b"},
        )
        app.state.mcp_service = service
        stdio_access.grant("user-a", {"mcp__fake__echo"})
        stdio_access.grant("user-b", set())
        provider.push(
            [
                StreamChunk(
                    id="s1",
                    model="deepseek-flash",
                    finish_reason="stop",
                    content="只列出",
                    usage=UsageStats(1, 1, 2),
                )
            ]
        )
        with client.stream(
            "POST",
            "/api/v1/chat/completions/stream",
            json=chat_payload(mode="agent"),
            headers={"Authorization": "Bearer token-b"},
        ) as response:
            assert response.status_code == 200
            response.read()
        offered = [item["function"]["name"] for item in (provider.calls[0].tools or [])]
        assert "mcp__fake__echo" not in offered
    if service is not None:
        asyncio.run(service.aclose())
