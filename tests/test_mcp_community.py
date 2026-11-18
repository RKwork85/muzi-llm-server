"""社区目录接入链路：谁装的归谁、令牌连接、卸载只断自己。

这些用例覆盖插件市场里最容易坏、也最容易「看起来装上了」的三段：
安装状态、按用户的「已安装」判定、以及非 OAuth 服务的令牌接入。
"""

from __future__ import annotations

import ipaddress

import httpx
import pytest

from tests.conftest import make_settings
from tests.support_agent_access import allow_legacy_agent_api
from tests.test_remote_mcp import _fake_app


@pytest.fixture(autouse=True)
def _legacy_agent_api(monkeypatch) -> None:
    allow_legacy_agent_api(monkeypatch)


from app.core.exceptions import ConnectorAuthRequiredError, InvalidRequestError
from app.mcp.service import McpService
from app.mcp.store import MemoryStore


async def _resolver(_host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    return [ipaddress.ip_address("127.0.0.1")]


def _service(client: httpx.AsyncClient) -> McpService:
    settings = make_settings(
        mcp_token_key="test-token-key-012345",
        mcp_allow_insecure=True,
        mcp_public_base_url="http://app.test",
        mcp_frontend_base_url="http://front.test",
        mcp_timeout_seconds=5,
    )
    # catalog=()：社区连接器走目录安装，避免静态目录的授权策略干扰断言。
    return McpService(settings, MemoryStore(), client, (), {"token-a": "user-a", "token-b": "user-b"}, _resolver)


async def _install(service: McpService) -> str:
    installed = await service.install_community(
        "acme/remote",
        "streamable_http",
        "http://mcp.test/mcp",
        "Acme Remote",
    )
    return str(installed["name"])


@pytest.mark.asyncio
async def test_allow_write_whitelist() -> None:
    """写工具默认关，只有部署侧白名单命中的连接器才开放。"""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_fake_app()), base_url="http://mcp.test") as client:
        service = _service(client)
        installed = await service.install_community(
            "acme/remote", "streamable_http", "http://mcp.test/mcp", "Acme"
        )
        assert installed["allow_write"] is False

        service.settings.mcp_community_allow_write = "acme/remote"
        again = await service.install_community("acme/remote", "streamable_http", "http://mcp.test/mcp", "Acme")
        assert again["allow_write"] is True
        connector = await service.resolve_connector(str(installed["name"]))
        assert connector.allow_write is True
        await service.aclose()


@pytest.mark.asyncio
async def test_last_error_recorded_on_reauth() -> None:
    """授权失效会把失败原因写进连接记录，卡片能长期展示「为什么连不上」。"""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_fake_app()), base_url="http://mcp.test") as client:
        service = _service(client)
        name = await _install(service)
        await service.connect_with_token("user-a", name, "user-a-secret-token")
        connection = await service.store.get_connection("user-a", name)
        assert connection is not None and connection.last_error is None

        await service._mark_reauth(connection, "测试失败原因")
        saved = await service.store.get_connection("user-a", name)
        assert saved is not None
        assert saved.status == "needs_reauth"
        assert saved.last_error == "测试失败原因"
        await service.aclose()


@pytest.mark.asyncio
async def test_install_reports_created_once() -> None:
    """第二次安装复用目录项：否则回滚条件永远不成立，失败会留下死卡片。"""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_fake_app()), base_url="http://mcp.test") as client:
        service = _service(client)
        first = await service.install_community("acme/remote", "streamable_http", "http://mcp.test/mcp", "Acme")
        second = await service.install_community("acme/remote", "streamable_http", "http://mcp.test/mcp", "Acme")
        assert first["status"] == "created"
        assert second["status"] == "existing"
        assert second["name"] == first["name"]
        await service.aclose()


@pytest.mark.asyncio
async def test_token_connection_and_per_user_installed_list() -> None:
    """令牌连接成功后，只有连接过的用户能看到「已安装」。"""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_fake_app()), base_url="http://mcp.test") as client:
        service = _service(client)
        name = await _install(service)

        tools = await service.connect_with_token("user-a", name, "user-a-secret-token")
        # 写类工具默认不勾：授权不等于同意让模型改数据。
        assert "echo" in tools
        assert "delete_page" not in tools

        stored = await service.store.get_connection("user-a", name)
        assert stored is not None
        assert "user-a-secret-token" not in stored.access_token_encrypted

        mine = await service.community_connectors("user-a")
        assert [(row["name"], row["status"]) for row in mine] == [(name, "connected")]
        assert await service.community_connectors("user-b") == []

        assert name in {row["name"] for row in await service.list_for("user-a")}
        assert name not in {row["name"] for row in await service.list_for("user-b")}
        await service.aclose()


@pytest.mark.asyncio
async def test_missing_connection_is_not_a_login_problem() -> None:
    """未连接要报 MCP_AUTH_REQUIRED(409)，不能再借用「请先登录」(401)。"""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_fake_app()), base_url="http://mcp.test") as client:
        service = _service(client)
        name = await _install(service)
        with pytest.raises(ConnectorAuthRequiredError):
            await service.discover("user-a", name)
        await service.aclose()


@pytest.mark.asyncio
async def test_uninstall_only_breaks_my_own_connection() -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_fake_app()), base_url="http://mcp.test") as client:
        service = _service(client)
        name = await _install(service)
        await service.connect_with_token("user-a", name, "user-a-secret-token")
        await service.connect_with_token("user-b", name, "user-b-secret-token")

        await service.uninstall_community(name, user_id="user-a")
        assert await service.store.get_connection("user-a", name) is None
        assert await service.store.get_connection("user-b", name) is not None
        assert await service.store.get_community_connector(name) is not None

        await service.uninstall_community(name, user_id="user-b")
        assert await service.store.get_community_connector(name) is None
        await service.aclose()


@pytest.mark.asyncio
async def test_token_connection_rejects_bad_input() -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_fake_app()), base_url="http://mcp.test") as client:
        service = _service(client)
        name = await _install(service)
        with pytest.raises(InvalidRequestError):
            await service.connect_with_token("user-a", name, "short")
        # 令牌会被拼进 Authorization 头，空白/控制字符等同头注入。
        with pytest.raises(InvalidRequestError):
            await service.connect_with_token("user-a", name, "good-token with spaces")
        await service.aclose()


@pytest.mark.asyncio
async def test_connect_with_custom_auth_headers() -> None:
    """自定义认证头连接：能走通握手，头值加密不落明文，保留头被拒。"""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_fake_app()), base_url="http://mcp.test") as client:
        service = _service(client)
        name = await _install(service)
        tools = await service.connect_with_credentials(
            "user-a", name, token="", headers={"X-API-Key": "secret-value-123"}
        )
        assert "echo" in tools

        stored = await service.store.get_connection("user-a", name)
        assert stored is not None
        assert "secret-value-123" not in stored.access_token_encrypted

        # 传输保留头不允许被当作认证头覆盖。
        with pytest.raises(InvalidRequestError):
            await service.connect_with_credentials("user-a", name, token="", headers={"Authorization": "x"})
        with pytest.raises(InvalidRequestError):
            await service.connect_with_credentials("user-a", name, token="", headers={"X-Bad Name": "x"})
        await service.aclose()


def test_auth_headers_injection() -> None:
    """空 token 不发 Authorization，自定义头原样带出。"""
    from app.mcp.http_client import RemoteSession, _headers

    session = RemoteSession(transport="streamable_http", url="http://mcp.test/mcp", token="", client=None)
    session.auth_headers = {"X-API-Key": "k"}
    headers = _headers(session)
    assert "Authorization" not in headers
    assert headers["X-API-Key"] == "k"

    session.token = "bearer-token"
    headers = _headers(session)
    assert headers["Authorization"] == "Bearer bearer-token"
