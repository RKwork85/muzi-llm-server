"""按用户和连接器分开的 HTTP 客户端。Cookie 不跨用户。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx

from app.core.config import Settings
from app.mcp.oauth_guard import OAuthFlowError, addresses_allowed, literal_addresses, system_resolver

Factory = Callable[[], httpx.AsyncClient]


class ClientPool:
    def __init__(self, factory: Factory) -> None:
        self._factory = factory
        self._clients: dict[tuple[str, str], httpx.AsyncClient] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    def get(self, user_id: str, name: str) -> httpx.AsyncClient:
        key = (user_id, name)
        client = self._clients.get(key)
        if client is None or client.is_closed:
            client = self._factory()
            self._clients[key] = client
        return client

    def peek(self, user_id: str, name: str) -> httpx.AsyncClient | None:
        client = self._clients.get((user_id, name))
        if client is None or client.is_closed:
            return None
        return client

    def lock(self, user_id: str, name: str) -> asyncio.Lock:
        key = (user_id, name)
        current = self._locks.get(key)
        if current is None:
            current = asyncio.Lock()
            self._locks[key] = current
        return current

    def uses_socket(self, user_id: str, name: str) -> bool:
        client = self.get(user_id, name)
        return not isinstance(client._transport, httpx.ASGITransport)

    async def discard(self, user_id: str, name: str) -> None:
        client = self._clients.pop((user_id, name), None)
        if client is not None and not client.is_closed:
            await client.aclose()

    async def aclose(self) -> None:
        clients = list(self._clients.values())
        self._clients.clear()
        for client in clients:
            if not client.is_closed:
                await client.aclose()


class _PinningBackend:
    """连接已检查过的地址，避免检查之后 DNS 重新指向内网。"""

    def __init__(self, inner: object, *, allow_insecure: bool) -> None:
        self._inner = inner
        self._allow_insecure = allow_insecure

    async def connect_tcp(self, host: str, port: int, timeout: float | None = None, local_address: str | None = None, socket_options: object = None):
        literal = literal_addresses(host)
        addresses = literal if literal is not None else await system_resolver(host)
        if not addresses_allowed(addresses, allow_insecure=self._allow_insecure):
            raise OAuthFlowError("endpoint_address")
        pinned = str(addresses[0])
        return await self._inner.connect_tcp(  # type: ignore[attr-defined]
            pinned,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options: object = None):
        del path, timeout, socket_options
        raise OAuthFlowError("endpoint_scheme")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)  # type: ignore[attr-defined]


def _production_client(settings: Settings) -> httpx.AsyncClient:
    client = httpx.AsyncClient(
        timeout=settings.mcp_timeout_seconds,
        follow_redirects=False,
        trust_env=False,
        cookies=httpx.Cookies(),
    )
    pool = client._transport._pool
    pool._network_backend = _PinningBackend(pool._network_backend, allow_insecure=settings.mcp_allow_insecure)
    return client


def pool_from_template(template: httpx.AsyncClient, settings: Settings) -> ClientPool:
    asgi_app = getattr(template._transport, "app", None)
    base_url = template.base_url

    def factory() -> httpx.AsyncClient:
        if asgi_app is not None:
            return httpx.AsyncClient(
                transport=httpx.ASGITransport(app=asgi_app),
                base_url=base_url,
                timeout=settings.mcp_timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                cookies=httpx.Cookies(),
            )
        return _production_client(settings)

    return ClientPool(factory)


def production_pool(settings: Settings) -> ClientPool:
    return ClientPool(lambda: _production_client(settings))
