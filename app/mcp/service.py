"""目录、OAuth 和按用户调用。不接收用户提交的服务地址。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
from base64 import urlsafe_b64encode
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse

import httpx
from fastapi import Request

from app.core.config import Settings
from app.core.exceptions import (
    ConnectorAuthRequiredError,
    ConnectorNotFoundError,
    InvalidRequestError,
    LoginRequiredError,
    McpUnavailableError,
)
from app.mcp.catalog import Connector, load_catalog
from app.mcp.community import search_community_hybrid, slug_connector_name
from app.mcp.crypto import TokenCipher
from app.mcp.execution import CURRENT_MCP_SCOPE, context_for, run_mcp
from app.mcp.http_client import release_hub, shared_hub
from app.mcp.keys import KeyConfigurationError, KeyRing
from app.mcp.observe import metrics
from app.mcp.oauth_guard import OAuthFlowError, issuers_equal, metadata_url_for_issuer, screen_endpoint, trusted_issuer
from app.mcp.protocol import MAX_TOOL_PAGES, ProtocolError, parse_tools_page, tools_list_params
from app.mcp.pool import ClientPool, pool_from_template
from app.mcp.store import (
    BridgeCredentialRecord,
    ClientRecord,
    CommunityConnectorRecord,
    ConnectionRecord,
    MemoryStore,
    SqlStore,
    StateRecord,
)
from app.models.mixins import utcnow
from app.tools.mcp_client import McpArgs, _description, _schema
from app.tools.registry import ToolRegistry, ToolSpec

logger = logging.getLogger("muzi_llm")
_WRITE = re.compile(r"(create|update|delete|insert|remove|write|append|patch|move|trash|destroy)", re.I)
_REMOTE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_LOCAL = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# 自定义认证头：只放行 RFC 7230 token 形状的头名，且不许覆盖传输层保留头。
_HEADER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")
_RESERVED_HEADERS = frozenset(
    {
        "authorization",
        "host",
        "content-length",
        "content-type",
        "accept",
        "mcp-protocol-version",
        "mcp-session-id",
        "connection",
        "transfer-encoding",
        "cookie",
        "set-cookie",
        "user-agent",
    }
)
Store = MemoryStore | SqlStore


class McpService:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        client: httpx.AsyncClient | ClientPool,
        catalog: tuple[Connector, ...] | None = None,
        users: dict[str, str] | None = None,
        resolver: object | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.pool = client if isinstance(client, ClientPool) else pool_from_template(client, settings)
        self.catalog = (
            catalog
            if catalog is not None
            else load_catalog(
                allow_insecure=settings.mcp_allow_insecure,
                env={"MCP_PUBLIC_BASE_URL": settings.mcp_public_base_url},
            )
        )
        self.users = users
        self._resolver = resolver
        self._cipher: TokenCipher | None = None
        self._keys: KeyRing | None = None

    def cipher(self) -> TokenCipher:
        if self._cipher is None:
            if len(self.settings.mcp_token_key.strip()) < 16:
                raise McpUnavailableError()
            self._cipher = TokenCipher(self.settings.mcp_token_key)
        return self._cipher

    def keys(self) -> KeyRing:
        if self._keys is None:
            self._keys = KeyRing(self.settings)
        return self._keys

    def _seal(self, plain: str, *, user_id: str, connector_id: str, credential_type: str) -> str:
        try:
            return self.keys().encrypt(plain, user_id=user_id, connector_id=connector_id, credential_type=credential_type)
        except KeyConfigurationError:
            # 服务端密钥没配好是部署问题，不是「用户没登录」。
            raise McpUnavailableError("连接服务密钥未配置，暂无法保存凭据") from None

    def _reveal(self, sealed: str, *, user_id: str, connector_id: str, credential_type: str) -> str:
        if sealed.startswith("v2."):
            return self.keys().decrypt(sealed, user_id=user_id, connector_id=connector_id, credential_type=credential_type)
        return self.cipher().decrypt(sealed)

    def connector(self, name: str) -> Connector:
        for item in self.catalog:
            if item.name == name:
                return item
        raise ConnectorNotFoundError()

    async def resolve_connector(self, name: str) -> Connector:
        """先查静态目录，再查社区安装的远程连接器。社区连接器 issuer 为空，走资源 origin。"""
        for item in self.catalog:
            if item.name == name:
                return item
        record = await self.store.get_community_connector(name)
        if record is not None:
            return self._from_community(record)
        raise ConnectorNotFoundError()

    def _from_community(self, record: CommunityConnectorRecord) -> Connector:
        return Connector(
            name=record.name,
            display_name=record.display_name,
            transport=record.transport,
            url=record.url,
            allow_write=record.allow_write,
            issuer="",
        )

    async def user_id_from_request(self, request: Request) -> str | None:
        raw = _bearer(request)
        if not raw:
            return None
        if self.users is not None:
            return self.users.get(raw)
        if not isinstance(self.store, SqlStore):
            return None
        from app.services.auth_service import AuthService

        user = await AuthService(self.store.session).user_for_token(raw)
        return None if user is None else str(user.id)

    async def require_user(self, request: Request) -> str:
        user_id = await self.user_id_from_request(request)
        if user_id is None:
            raise LoginRequiredError()
        return user_id

    async def list_for(self, user_id: str | None) -> list[dict[str, Any]]:
        connections = {item.connector_name: item for item in await self.store.list_connections(user_id)} if user_id else {}
        items: list[tuple[Connector, bool]] = [(item, False) for item in self.catalog]
        for record in await self.store.list_community_connectors():
            # 社区目录项是全局的，但「我的连接器列表」只属于连接过它的用户：
            # 否则别人装的东西会出现在我的面板里，点连接还会因为没授权而失败。
            if record.name in connections:
                items.append((self._from_community(record), True))
        rows: list[dict[str, Any]] = []
        for item, community in items:
            connection = connections.get(item.name)
            rows.append(
                {
                    "name": item.name,
                    "display_name": item.display_name,
                    # 前端用它区分「平台内置连接器」与「社区安装」，避免再维护一份本地名单。
                    "community": community,
                    "transport": item.transport,
                    "status": connection.status if connection else "disconnected",
                    "selected_tools": list(connection.selected_tools) if connection else [],
                    "last_error": connection.last_error if connection and connection.last_error else "",
                    "allow_write": item.allow_write,
                }
            )
        return rows

    async def begin(self, user_id: str, name: str) -> str:
        connector = await self.resolve_connector(name)
        metadata = await self._metadata(user_id, connector)
        client = await self._client_registration(user_id, connector, metadata)
        verifier = secrets.token_urlsafe(32)
        state = secrets.token_urlsafe(24)
        digest = hashlib.sha256(verifier.encode("utf-8")).digest()
        challenge = urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        sealed = self._seal(
            json.dumps(
                {
                    "v": 1,
                    "verifier": verifier,
                    "issuer": metadata["issuer"],
                    "token_endpoint": metadata["token_endpoint"],
                    "iss_required": metadata["iss_required"],
                },
                separators=(",", ":"),
            ),
            user_id=user_id,
            connector_id=name,
            credential_type="pkce",
        )
        await self.store.save_state(
            StateRecord(
                state_hash=hashlib.sha256(state.encode("utf-8")).hexdigest(),
                user_id=user_id,
                connector_name=name,
                code_verifier_encrypted=sealed,
                expires_at=utcnow() + timedelta(minutes=10),
            )
        )
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client.client_id,
                "redirect_uri": self._redirect_uri(),
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
                "resource": connector.url,
            }
        )
        return f"{metadata['authorization_endpoint']}?{query}"

    async def finish(self, code: str, state: str, iss: str = "") -> str:
        if not code or not state:
            raise ConnectorAuthRequiredError("授权回跳缺少 code/state，请重新发起连接")
        saved = await self.store.pop_state(hashlib.sha256(state.encode("utf-8")).hexdigest())
        if saved is None:
            # state 10 分钟过期或被用过：这是流程过期，不是平台会话失效。
            raise ConnectorAuthRequiredError("授权已过期，请重新发起连接")
        connector = await self.resolve_connector(saved.connector_name)
        bound = self._open_verifier(saved.code_verifier_encrypted, saved.user_id, saved.connector_name)
        if not self._iss_matches(bound, iss):
            logger.info("mcp oauth rejected connector=%s reason=issuer_mismatch", connector.name)
            raise ConnectorAuthRequiredError("授权校验失败，请重新发起连接")
        client = await self.store.get_client(connector.name)
        if client is None:
            raise ConnectorAuthRequiredError("授权客户端不存在，请重新发起连接")
        endpoint = await self._screen(saved.user_id, connector.name, bound["token_endpoint"])
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._redirect_uri(),
            "client_id": client.client_id,
            "code_verifier": bound["verifier"],
            "resource": connector.url,
        }
        if client.client_secret_encrypted:
            form["client_secret"] = self._reveal(
                client.client_secret_encrypted,
                user_id="connector",
                connector_id=connector.name,
                credential_type="client_secret",
            )
        try:
            token = await self._token(saved.user_id, connector.name, endpoint, form)
        except Exception:
            # 回跳已经拿到 code 但换票失败（端点 4xx/跳转/畸形响应）：用户要做的
            # 仍然是「重新发起一次授权」，不该被翻译成平台登录问题。
            logger.info("mcp oauth token exchange failed connector=%s", connector.name)
            raise ConnectorAuthRequiredError("授权换取令牌失败，请重新发起连接") from None
        await self._save_token(
            saved.user_id,
            connector.name,
            token,
            previous=None,
            binding={"issuer": bound["issuer"], "token_endpoint": endpoint},
        )
        await self._drop_client(saved.user_id, connector.name)
        await self._apply_default_selection(saved.user_id, connector.name)
        return connector.name

    async def discover(self, user_id: str, name: str) -> list[dict[str, Any]]:
        connector = await self.resolve_connector(name)
        listed = await self._list(user_id, connector)
        tools: list[dict[str, Any]] = []
        for item in listed:
            remote = item.get("name")
            if not isinstance(remote, str) or _REMOTE.fullmatch(remote) is None:
                continue
            tools.append(
                {
                    "name": remote,
                    "description": _description(item.get("description")),
                    "writable": _WRITE.search(remote) is not None,
                    "offered": connector.allow_write or _WRITE.search(remote) is None,
                    "input_schema": item.get("inputSchema"),
                }
            )
        return tools

    async def select(self, user_id: str, name: str, names: list[str]) -> list[str]:
        available = {item["name"] for item in await self.discover(user_id, name) if item["offered"]}
        chosen = [item for item in names if item in available]
        connection = await self._require_connection(user_id, name)
        connection.selected_tools = chosen
        await self.store.save_connection(connection)
        return chosen

    async def disconnect(self, user_id: str, name: str) -> None:
        await self.resolve_connector(name)
        await self.store.delete_connection(user_id, name)
        await self.store.delete_bridge_credential(user_id, name)
        await self._drop_client(user_id, name)

    async def configure_bridge(self, user_id: str, name: str, credentials: dict[str, Any]) -> None:
        """保存多租户 bridge（企微/飞书）凭据并签发绑定用户的 MCP 连接。"""
        await self.resolve_connector(name)
        sealed = self.keys().encrypt(
            json.dumps(credentials, separators=(",", ":")),
            user_id=user_id,
            connector_id=name,
            credential_type="bridge_credentials",
        )
        await self.store.save_bridge_credential(BridgeCredentialRecord(user_id, name, sealed))
        if await self.store.get_client(name) is None:
            await self.store.save_client(ClientRecord(connector_name=name, client_id=f"bridge-{name}"))
        from app.bridge.server import issue_bridge_tokens

        tokens = issue_bridge_tokens(self.settings, user_id, name, sealed)
        base = self.settings.mcp_public_base_url.rstrip("/")
        await self._save_token(
            user_id,
            name,
            tokens,
            previous=None,
            binding={
                "issuer": f"{base}/bridge/{name}",
                "token_endpoint": f"{base}/bridge/{name}/token",
            },
        )
        await self._apply_default_selection(user_id, name)

    async def _apply_default_selection(self, user_id: str, name: str, *, strict: bool = False) -> None:
        """刚连上就勾好只读工具，避免「授权成功但还是什么都调不了」。

        写入/删除类工具不在默认勾选里：OAuth 只代表同意授权，不代表同意让模型
        改数据，那一步要用户自己在连接器面板里勾。默认失败不影响连接本身；
        `strict=True` 时失败会抛出（令牌模式需要它来校验令牌是否真的可用）。
        """
        try:
            connection = await self.store.get_connection(user_id, name)
            if connection is None or connection.status != "connected" or connection.selected_tools:
                return
            listed = await self.discover(user_id, name)
            readonly = [item["name"] for item in listed if item["offered"] and not item["writable"]]
            if not readonly:
                return
            connection.selected_tools = readonly
            await self.store.save_connection(connection)
        except Exception:  # noqa: BLE001 - 默认勾选是便利功能，不能反过来毁掉连接
            logger.info("mcp default selection skipped connector=%s", name)
            if strict:
                raise

    async def connect_with_token(self, user_id: str, name: str, token: str) -> list[str]:
        """用用户自带的访问令牌完成连接（兼容入口，等价于 Bearer 头）。"""
        return await self.connect_with_credentials(user_id, name, token=token, headers={})

    async def connect_with_credentials(
        self,
        user_id: str,
        name: str,
        *,
        token: str = "",
        headers: dict[str, str] | None = None,
    ) -> list[str]:
        """用用户自带的凭据（Bearer 令牌 / 自定义认证头）完成连接。

        适用于没有标准 OAuth 元数据、只认 `Authorization: Bearer <token>` 或
        `X-API-Key` 这类自定义头的远程 MCP（官方目录里占了很大一部分）。
        凭据与 OAuth 访问令牌同样加密入库，过期后由用户重新粘贴：
        平台不替用户去第三方后台换票。
        """
        header_map = {str(k).strip(): str(v) for k, v in (headers or {}).items() if k and v is not None}
        value = token.strip()
        if not value and not header_map:
            raise InvalidRequestError("请提供访问令牌或认证头")
        if value and len(value) < 8:
            raise InvalidRequestError("访问令牌过短")
        # 令牌会被拼进 Authorization 头：任何空白/控制字符都可能是头注入。
        if value and any(ch.isspace() or not ch.isprintable() for ch in value):
            raise InvalidRequestError("访问令牌包含非法字符")
        # 认证头逐个校验：头名形状 + 保留头 + 头值无 CR/LF。
        for header_name, header_value in header_map.items():
            if not _HEADER_NAME.fullmatch(header_name):
                raise InvalidRequestError(f"非法认证头名：{header_name}")
            if header_name.lower() in _RESERVED_HEADERS:
                raise InvalidRequestError(f"认证头 {header_name} 由平台保留，不能自定义")
            if "\r" in header_value or "\n" in header_value or not header_value.strip():
                raise InvalidRequestError(f"认证头 {header_name} 的值非法")
        if any(item.name == name for item in self.catalog):
            raise InvalidRequestError("该连接器使用平台内置授权方式，不能填写访问凭据")
        connector = await self.resolve_connector(name)
        sealed = self._seal(
            json.dumps(
                {"v": 1, "token": value, "issuer": "", "token_endpoint": "", "headers": header_map},
                separators=(",", ":"),
            ),
            user_id=user_id,
            connector_id=connector.name,
            credential_type="access_token",
        )
        previous = await self.store.get_connection(user_id, name)
        await self.store.save_connection(
            ConnectionRecord(
                user_id=user_id,
                connector_name=name,
                status="connected",
                access_token_encrypted=sealed,
                refresh_token_encrypted=None,
                expires_at=None,
                selected_tools=list(previous.selected_tools) if previous else [],
            )
        )
        try:
            await self._apply_default_selection(user_id, name, strict=True)
        except Exception:
            await self.store.delete_connection(user_id, name)
            raise ConnectorAuthRequiredError("凭据无法访问该服务，请检查令牌与地址") from None
        saved = await self.store.get_connection(user_id, name)
        return list(saved.selected_tools) if saved is not None else []

    async def bridge_credentials_status(self, user_id: str, name: str) -> dict[str, Any]:
        """返回该连接器凭据是否已配置，以及用于权限开通的公开标识。绝不返回密钥。"""
        record = await self.store.get_bridge_credential(user_id, name)
        result: dict[str, Any] = {"configured": record is not None, "connector": name}
        if record is not None:
            try:
                plain = self.keys().decrypt(
                    record.credentials_encrypted,
                    user_id=user_id,
                    connector_id=name,
                    credential_type="bridge_credentials",
                )
                data = json.loads(plain)
                if isinstance(data, dict):
                    if name == "feishu" and str(data.get("app_id", "")).strip():
                        result["app_id"] = str(data["app_id"]).strip()
                    elif name == "wecom" and str(data.get("corp_id", "")).strip():
                        result["corp_id"] = str(data["corp_id"]).strip()
            except (KeyConfigurationError, ValueError, json.JSONDecodeError):
                pass
        return result

    async def search_community(
        self, query: str, sort: str, limit: int, page: int, installable_only: bool = False
    ) -> dict[str, Any]:
        """搜索社区目录：awesome 全文搜索 + 热度排序 + 交叉匹配官方 remote 端点。"""
        return await search_community_hybrid(
            query,
            sort,
            limit,
            page,
            base_url=self.settings.mcp_registry_base_url,
            installable_only=installable_only,
        )

    async def community_connectors(self, user_id: str | None) -> list[dict[str, Any]]:
        """当前用户「已安装」的社区连接器。

        「已安装」的判定是**这个用户自己的连接记录**，不是平台目录里存在这一行：
        目录项是全局的（一次安装全站可复用，OAuth client 也只注册一次），但
        「装没装」属于用户。之前直接列全表，会把他人的安装显示成我的未连接卡片，
        卸载时还会连带删掉别人的授权。
        """
        connections = (
            {item.connector_name: item for item in await self.store.list_connections(user_id)} if user_id else {}
        )
        rows: list[dict[str, Any]] = []
        for record in await self.store.list_community_connectors():
            connection = connections.get(record.name)
            if connection is None:
                continue
            rows.append(
                {
                    "name": record.name,
                    "qualified_name": record.qualified_name,
                    "display_name": record.display_name,
                    "description": record.description,
                    "transport": record.transport,
                    "url": record.url,
                    "icon_url": record.icon_url or "",
                    "website_url": record.website_url or "",
                    "auth_headers": record.auth_headers or "",
                    "allow_write": record.allow_write,
                    "status": connection.status,
                    "last_error": connection.last_error or "",
                    "selected_tools": list(connection.selected_tools),
                }
            )
        return rows

    async def install_community(
        self,
        qualified_name: str,
        transport: str,
        url: str,
        display_name: str,
        description: str = "",
        icon_url: str = "",
        website_url: str = "",
        auth_headers: str = "",
    ) -> dict[str, Any]:
        """把社区远程连接器登记为平台目录项。URL 必须通过 SSRF 护栏校验。

        返回的 ``status`` 区分「本次新建」与「已存在」：调用方需要在后续授权失败时
        决定是否回滚。之前两种情况都返回 ``installed``，导致回滚条件永远不成立，
        于是失败会留下「已安装但永远连不上」的死卡片。
        """
        if transport not in {"streamable_http", "sse"}:
            raise InvalidRequestError("不支持的传输类型")
        try:
            screened = await screen_endpoint(
                url,
                allow_insecure=self.settings.mcp_allow_insecure,
                resolver=self._resolver,
                allow_unresolved_name=False,
            )
        except OAuthFlowError:
            raise InvalidRequestError("连接地址不可用") from None
        base = slug_connector_name(qualified_name)
        name = base
        existing = await self.store.get_community_connector(name)
        if existing is not None and existing.qualified_name == qualified_name:
            # 白名单变更后同步：安装入口是重新校验 allow_write 的时机。
            should_write = qualified_name in self.settings.community_write_allowlist()
            if existing.allow_write != should_write:
                existing.allow_write = should_write
                await self.store.save_community_connector(existing)
            return {
                "name": existing.name,
                "qualified_name": qualified_name,
                "status": "existing",
                "allow_write": existing.allow_write,
            }
        if existing is not None or any(item.name == name for item in self.catalog):
            name = self._dedupe_name(base, qualified_name)
        record = CommunityConnectorRecord(
            name=name,
            qualified_name=qualified_name,
            display_name=(display_name.strip()[:100] or base),
            description=description.strip()[:200],
            transport=transport,
            url=screened,
            icon_url=(icon_url.strip()[:255] or None),
            website_url=(website_url.strip()[:255] or None),
            auth_headers=auth_headers.strip()[:255],
            allow_write=qualified_name in self.settings.community_write_allowlist(),
        )
        await self.store.save_community_connector(record)
        return {"name": name, "qualified_name": qualified_name, "status": "created", "allow_write": record.allow_write}

    async def uninstall_community(self, name: str, *, user_id: str | None = None) -> None:
        """卸载。

        `user_id` 给定时只断这个用户自己的连接（「卸载」对用户而言是断连），
        目录项与其他用户的授权保留。没有 `user_id` 是内部回滚：整条目录项删掉。
        """
        record = await self.store.get_community_connector(name)
        if record is None:
            raise ConnectorNotFoundError()
        if user_id is not None:
            await self.store.delete_connection(user_id, name)
            if await self.store.connector_in_use(name):
                return
        await self.store.delete_community_connector(name)
        await self.store.delete_connections_by_connector(name)
        await self.store.delete_client_by_connector(name)

    def _dedupe_name(self, base: str, qualified_name: str) -> str:
        digest = hashlib.sha1(qualified_name.encode("utf-8")).hexdigest()[:8]
        return f"{base[:23]}_{digest}"

    async def authorized_connectors(self, user_id: str | None) -> frozenset[str]:
        """该用户「授权了并且勾了工具」的连接器。

        与 `tool_specs` 用同一个前置判断（status=connected 且 selected_tools 非空），
        但只读库、不发网络请求，所以可以放在 `/me/capabilities` 这种高频接口里。
        读不到就返回空集：插件面板宁可少显示，也不承诺一件没法确认的事。
        """
        if not user_id:
            return frozenset()
        try:
            rows = await self.store.list_connections(user_id)
        except Exception:  # noqa: BLE001 - 权限投影不能因为库抖动而放大能力
            logger.info("mcp connector lookup skipped")
            return frozenset()
        return frozenset(
            row.connector_name
            for row in rows
            if row.status == "connected" and row.selected_tools
        )

    async def tool_specs(self, user_id: str) -> list[ToolSpec]:
        specs: list[ToolSpec] = []
        for connection in await self.store.list_connections(user_id):
            if connection.status != "connected":
                continue
            try:
                connector = await self.resolve_connector(connection.connector_name)
            except ConnectorNotFoundError:
                continue
            listed = {
                item["name"]: item
                for item in await self.discover(user_id, connector.name)
                if item["offered"]
            }
            for remote in connection.selected_tools:
                tool = listed.get(remote)
                if tool is None:
                    continue
                local = f"mcp__{connector.name}__{remote}"
                if _LOCAL.fullmatch(local) is None:
                    continue
                specs.append(self._spec(user_id, connector, tool, local))
        return specs

    async def _list(self, user_id: str, connector: Connector) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(MAX_TOOL_PAGES):
            message = await self._rpc(user_id, connector, "tools/list", tools_list_params(cursor))
            try:
                page, cursor = parse_tools_page(message.get("result"))
            except ProtocolError:
                return tools
            tools.extend(page)
            if cursor is None:
                return tools
        logger.info("mcp tools list truncated connector=%s", connector.name)
        return tools

    async def _rpc(self, user_id: str, connector: Connector, method: str, params: dict[str, Any]) -> dict[str, Any]:
        connection = await self._require_connection(user_id, connector.name)
        async with self.pool.lock(user_id, connector.name):
            token = await self._access_token(connection, connector)
            auth_headers = self._auth_headers(connection, connector)
            http = self.pool.get(user_id, connector.name)
        try:
            return await shared_hub(http).call(
                user_id,
                connector.name,
                connector.transport,
                connector.url,
                token,
                method,
                params,
                auth_headers=auth_headers,
            )
        except PermissionError:
            await self._mark_reauth(connection)
            raise ConnectorAuthRequiredError("该连接器的授权已失效，请重新授权") from None

    def _auth_headers(self, connection: ConnectionRecord, connector: Connector) -> dict[str, str]:
        """从连接凭据里解出用户自定义的认证头。无则返回空字典。"""
        opened = self._open_access(connection.access_token_encrypted, connection.user_id, connector.name)
        raw = opened.get("headers")
        if not isinstance(raw, dict):
            return {}
        return {str(k): str(v) for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}

    async def _access_token(self, connection: ConnectionRecord, connector: Connector) -> str:
        opened = self._open_access(connection.access_token_encrypted, connection.user_id, connector.name)
        expired = connection.expires_at is not None and connection.expires_at <= utcnow() + timedelta(seconds=30)
        if not expired and opened["token"]:
            return opened["token"]
        # 纯自定义认证头连接：没有 bearer token 也不需要刷新，直接放行。
        if opened.get("headers"):
            return opened["token"]
        if not connection.refresh_token_encrypted:
            await self._mark_reauth(connection, "该连接器的授权已过期，请重新授权")
            raise ConnectorAuthRequiredError("该连接器的授权已过期，请重新授权")
        endpoint = opened["token_endpoint"]
        issuer = opened["issuer"]
        if not endpoint:
            metadata = await self._metadata(connection.user_id, connector)
            endpoint = metadata["token_endpoint"]
            issuer = metadata["issuer"]
        else:
            endpoint = await self._screen(connection.user_id, connector.name, endpoint)
        client = await self.store.get_client(connector.name)
        if client is None:
            await self._mark_reauth(connection, "该连接器的授权客户端已失效，请重新授权")
            raise ConnectorAuthRequiredError("该连接器的授权客户端已失效，请重新授权")
        form = {
            "grant_type": "refresh_token",
            "refresh_token": self._reveal(
                connection.refresh_token_encrypted,
                user_id=connection.user_id,
                connector_id=connector.name,
                credential_type="refresh_token",
            ),
            "client_id": client.client_id,
            "resource": connector.url,
        }
        if client.client_secret_encrypted:
            form["client_secret"] = self._reveal(
                client.client_secret_encrypted,
                user_id="connector",
                connector_id=connector.name,
                credential_type="client_secret",
            )
        try:
            token = await self._token(connection.user_id, connector.name, endpoint, form)
        except Exception:
            metrics.add("mcp_token_refresh_failures_total")
            logger.info("mcp refresh failed connector=%s", connector.name)
            await self._mark_reauth(connection, "该连接器的授权已过期，请重新授权")
            raise ConnectorAuthRequiredError("该连接器的授权已过期，请重新授权") from None
        await self._save_token(
            connection.user_id,
            connector.name,
            token,
            previous=connection,
            binding={"issuer": issuer, "token_endpoint": endpoint},
        )
        await self._drop_client(connection.user_id, connector.name)
        return token["access_token"]

    async def _save_token(
        self,
        user_id: str,
        name: str,
        token: dict[str, Any],
        previous: ConnectionRecord | None,
        binding: dict[str, str],
    ) -> None:
        access = token.get("access_token")
        if not isinstance(access, str) or not access:
            raise ConnectorAuthRequiredError("授权服务没有返回访问令牌，请重新授权")
        refresh = token.get("refresh_token")
        if not isinstance(refresh, str):
            refresh = None
        expires_in = token.get("expires_in")
        expires_at = utcnow() + timedelta(seconds=int(expires_in)) if isinstance(expires_in, int) else None
        selected = previous.selected_tools if previous else []
        if refresh is None and previous and previous.refresh_token_encrypted:
            refresh_encrypted = previous.refresh_token_encrypted
        else:
            refresh_encrypted = (
                self._seal(refresh, user_id=user_id, connector_id=name, credential_type="refresh_token") if refresh else None
            )
        sealed = self._seal(
            json.dumps(
                {
                    "v": 1,
                    "token": access,
                    "issuer": binding["issuer"],
                    "token_endpoint": binding["token_endpoint"],
                },
                separators=(",", ":"),
            ),
            user_id=user_id,
            connector_id=name,
            credential_type="access_token",
        )
        await self.store.save_connection(
            ConnectionRecord(
                user_id=user_id,
                connector_name=name,
                status="connected",
                access_token_encrypted=sealed,
                refresh_token_encrypted=refresh_encrypted,
                expires_at=expires_at,
                selected_tools=selected,
            )
        )

    async def _mark_reauth(self, connection: ConnectionRecord, reason: str = "该连接器的授权已失效，请重新授权") -> None:
        connection.status = "needs_reauth"
        connection.last_error = reason
        await self.store.save_connection(connection)

    async def _require_connection(self, user_id: str, name: str) -> ConnectionRecord:
        connection = await self.store.get_connection(user_id, name)
        if connection is None or connection.status != "connected":
            raise ConnectorAuthRequiredError("该连接尚未完成授权，请先连接")
        return connection

    async def _metadata(self, user_id: str, connector: Connector) -> dict[str, str]:
        """校验并返回连接器的标准 OAuth 元数据。

        服务端不支持标准 MCP OAuth（缺失 oauth-protected-resource / authorization-server、
        端点 404、issuer 不匹配）时统一抛 ``McpUnavailableError``，向用户说明「该服务不支持
        平台一键连接」。这里绝不能把 HTTP 4xx 冒泡成 ``LoginRequiredError``：用户其实
        已经登录，只是这个第三方端点不接受标准授权，报「请先登录」会把人带偏。
        """
        try:
            anchor = trusted_issuer(connector.url, connector.issuer)
            await self._screen(user_id, connector.name, connector.url)
            protected = await self._protected_metadata(user_id, connector)
        except (OAuthFlowError, LoginRequiredError) as exc:
            logger.info("mcp oauth rejected connector=%s reason=%s", connector.name, getattr(exc, "reason", "metadata"))
            raise McpUnavailableError("该服务不支持平台一键连接") from None
        servers = protected.get("authorization_servers") if isinstance(protected, dict) else None
        announced = [item for item in servers if isinstance(item, str)] if isinstance(servers, list) else []
        if not any(issuers_equal(anchor, item) for item in announced):
            logger.info("mcp oauth rejected connector=%s reason=issuer_unlisted", connector.name)
            raise McpUnavailableError("该服务不支持平台一键连接")
        try:
            document_url = await self._screen(user_id, connector.name, metadata_url_for_issuer(anchor))
            document = await self._json(user_id, connector.name, document_url)
            authorization = document.get("authorization_endpoint")
            token_endpoint = document.get("token_endpoint")
            if not isinstance(authorization, str) or not isinstance(token_endpoint, str):
                raise McpUnavailableError("该服务不支持平台一键连接")
            authorization = await self._screen(user_id, connector.name, authorization)
            token_endpoint = await self._screen(user_id, connector.name, token_endpoint)
            registration = document.get("registration_endpoint")
            if isinstance(registration, str) and registration:
                registration = await self._screen(user_id, connector.name, registration)
            else:
                registration = ""
        except (OAuthFlowError, LoginRequiredError) as exc:
            logger.info("mcp oauth rejected connector=%s reason=%s", connector.name, getattr(exc, "reason", "metadata"))
            raise McpUnavailableError("该服务不支持平台一键连接") from None
        if not issuers_equal(anchor, str(document.get("issuer") or "")):
            logger.info("mcp oauth rejected connector=%s reason=issuer_mismatch", connector.name)
            raise McpUnavailableError("该服务不支持平台一键连接")
        return {
            "authorization_endpoint": authorization,
            "token_endpoint": token_endpoint,
            "registration_endpoint": registration,
            "issuer": anchor,
            "iss_required": "1" if document.get("authorization_response_iss_parameter_supported") is True else "",
        }

    async def _client_registration(self, user_id: str, connector: Connector, metadata: dict[str, str]) -> ClientRecord:
        existing = await self.store.get_client(connector.name)
        if existing is not None:
            return existing
        # 预注册客户端：目录里显式配了 client_id（可选 client_secret），
        # 就绕开 DCR。官方目录里部分服务不开放动态注册，只能走这条路。
        if connector.client_id:
            record = ClientRecord(
                connector_name=connector.name,
                client_id=connector.client_id,
                client_secret_encrypted=(
                    self._seal(
                        connector.client_secret,
                        user_id="connector",
                        connector_id=connector.name,
                        credential_type="client_secret",
                    )
                    if connector.client_secret
                    else None
                ),
            )
            await self.store.save_client(record)
            return record
        if not metadata["registration_endpoint"]:
            raise McpUnavailableError("该服务不支持平台一键连接")
        try:
            response = await self._post(
                user_id,
                connector.name,
                metadata["registration_endpoint"],
                json={
                    "client_name": "muzi-llm",
                    "redirect_uris": [self._redirect_uri()],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                },
            )
        except OAuthFlowError as exc:
            logger.info("mcp oauth rejected connector=%s reason=%s", connector.name, exc.reason)
            raise McpUnavailableError("该服务不支持平台一键连接") from None
        body = response.json()
        client_id = body.get("client_id") if isinstance(body, dict) else None
        if not isinstance(client_id, str) or not client_id:
            raise McpUnavailableError("该服务不支持平台一键连接")
        secret = body.get("client_secret") if isinstance(body, dict) else None
        record = ClientRecord(
            connector_name=connector.name,
            client_id=client_id,
            client_secret_encrypted=(
                self._seal(secret, user_id="connector", connector_id=connector.name, credential_type="client_secret")
                if isinstance(secret, str) and secret
                else None
            ),
        )
        await self.store.save_client(record)
        return record

    async def _token(self, user_id: str, name: str, endpoint: str, form: dict[str, str]) -> dict[str, Any]:
        response = await self._post(user_id, name, endpoint, data=form)
        body = response.json()
        if not isinstance(body, dict):
            raise ConnectorAuthRequiredError("授权服务返回了无法解析的响应，请重新授权")
        return body

    async def _protected_metadata(self, user_id: str, connector: Connector) -> dict[str, Any]:
        pointed = await self._pointed_metadata(user_id, connector)
        if pointed:
            try:
                screened = await self._screen(user_id, connector.name, pointed)
                return await self._json(user_id, connector.name, screened)
            except OAuthFlowError:
                pass
        parsed = urlparse(connector.url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path if parsed.path not in {"", "/"} else ""
        # RFC 9728 允许把元数据挂在资源路径下。官方目录里两种写法都有，
        # 只试 origin 会把一批本来能一键授权的服务误判成「不支持」。
        candidates = [f"{origin}/.well-known/oauth-protected-resource{path}"]
        if path:
            candidates.append(f"{origin}/.well-known/oauth-protected-resource")
        last: OAuthFlowError | None = None
        for candidate in candidates:
            try:
                fallback = await self._screen(user_id, connector.name, candidate)
                return await self._json(user_id, connector.name, fallback)
            except OAuthFlowError as exc:
                last = exc
        raise last or OAuthFlowError("metadata_missing")

    async def _pointed_metadata(self, user_id: str, connector: Connector) -> str | None:
        http = self.pool.get(user_id, connector.name)
        request = http.build_request(
            "GET",
            connector.url,
            headers={"Accept": "application/json, text/event-stream"},
        )
        try:
            response = await http.send(request, stream=True, follow_redirects=False)
        except (httpx.HTTPError, OAuthFlowError):
            return None
        try:
            if response.status_code != 401 or 300 <= response.status_code < 400:
                return None
            return _resource_metadata(response.headers.get("www-authenticate", ""), connector.url)
        finally:
            await response.aclose()

    async def _json(self, user_id: str, name: str, url: str) -> dict[str, Any]:
        response = await self._get(user_id, name, url)
        body = response.json()
        return body if isinstance(body, dict) else {}

    async def _get(self, user_id: str, name: str, url: str) -> httpx.Response:
        try:
            response = await self.pool.get(user_id, name).get(
                url,
                headers={"Accept": "application/json"},
                follow_redirects=False,
            )
        except OAuthFlowError as exc:
            logger.info("mcp oauth rejected connector=%s reason=%s", name, exc.reason)
            raise OAuthFlowError(exc.reason) from None
        self._reject_redirect(name, response)
        if response.status_code >= 400:
            # 元数据端点 404/401 只说明「这家服务没有标准 OAuth 文档」，
            # 不是用户会话失效。用 OAuthFlowError 让上层翻译成准确文案。
            raise OAuthFlowError(f"http_{response.status_code}")
        return response

    async def _post(self, user_id: str, name: str, url: str, **kwargs: object) -> httpx.Response:
        try:
            response = await self.pool.get(user_id, name).post(url, follow_redirects=False, **kwargs)
        except OAuthFlowError as exc:
            logger.info("mcp oauth rejected connector=%s reason=%s", name, exc.reason)
            raise OAuthFlowError(exc.reason) from None
        self._reject_redirect(name, response)
        if response.status_code >= 400:
            raise OAuthFlowError(f"http_{response.status_code}")
        return response

    async def _screen(self, user_id: str, name: str, url: str) -> str:
        return await screen_endpoint(
            url,
            allow_insecure=self.settings.mcp_allow_insecure,
            resolver=self._resolver,
            allow_unresolved_name=not self.pool.uses_socket(user_id, name),
        )

    def _reject_redirect(self, name: str, response: httpx.Response) -> None:
        if 300 <= response.status_code < 400:
            logger.info("mcp oauth rejected connector=%s reason=redirect", name)
            raise OAuthFlowError("redirect")

    def _open_verifier(self, sealed: str, user_id: str, connector_id: str) -> dict[str, str]:
        try:
            data = json.loads(self._reveal(sealed, user_id=user_id, connector_id=connector_id, credential_type="pkce"))
        except (ValueError, json.JSONDecodeError, KeyConfigurationError):
            raise ConnectorAuthRequiredError("授权上下文已失效，请重新发起连接") from None
        if not isinstance(data, dict) or data.get("v") != 1:
            raise ConnectorAuthRequiredError("授权上下文已失效，请重新发起连接")
        verifier = data.get("verifier")
        issuer = data.get("issuer")
        endpoint = data.get("token_endpoint")
        if not isinstance(verifier, str) or not isinstance(issuer, str) or not isinstance(endpoint, str):
            raise ConnectorAuthRequiredError("授权上下文已失效，请重新发起连接")
        return {
            "verifier": verifier,
            "issuer": issuer,
            "token_endpoint": endpoint,
            "iss_required": "1" if data.get("iss_required") else "",
        }

    def _open_access(self, sealed: str, user_id: str, connector_id: str) -> dict[str, Any]:
        try:
            raw = self._reveal(sealed, user_id=user_id, connector_id=connector_id, credential_type="access_token")
        except (ValueError, KeyConfigurationError):
            return {"token": "", "issuer": "", "token_endpoint": "", "headers": {}}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {"token": raw, "issuer": "", "token_endpoint": "", "headers": {}}
        if not isinstance(data, dict) or data.get("v") != 1:
            return {"token": raw, "issuer": "", "token_endpoint": "", "headers": {}}
        token = data.get("token")
        issuer = data.get("issuer")
        endpoint = data.get("token_endpoint")
        headers = data.get("headers")
        return {
            "token": token if isinstance(token, str) else "",
            "issuer": issuer if isinstance(issuer, str) else "",
            "token_endpoint": endpoint if isinstance(endpoint, str) else "",
            "headers": headers if isinstance(headers, dict) else {},
        }

    def _iss_matches(self, bound: dict[str, str], iss: str) -> bool:
        if iss:
            return issuers_equal(bound["issuer"], iss)
        return not bound["iss_required"]

    async def _drop_client(self, user_id: str, name: str) -> None:
        client = self.pool.peek(user_id, name)
        if client is not None:
            await shared_hub(client).close_session(user_id, name)
            release_hub(client)
        await self.pool.discard(user_id, name)

    async def aclose(self) -> None:
        await self.pool.aclose()

    def _redirect_uri(self) -> str:
        return self.settings.mcp_public_base_url.rstrip("/") + "/api/v1/mcp/oauth/callback"

    def _spec(self, user_id: str, connector: Connector, tool: dict[str, Any], local: str) -> ToolSpec:
        remote = str(tool["name"])
        writable = bool(tool.get("writable")) and connector.allow_write
        schema = _schema(tool.get("input_schema"))
        required = list(schema.get("required") or [])
        properties = list(schema["properties"])

        async def handler(
            args: McpArgs,
            _user: str = user_id,
            _connector: Connector = connector,
            _remote: str = remote,
            _required: list[str] = required,
            _properties: list[str] = properties,
        ) -> str:
            from app.authorization.application.execution_guard import mcp_runtime_allowed

            if not mcp_runtime_allowed():
                raise PermissionError("mcp user denied")
            data = args.model_dump()
            if any(key not in data for key in _required):
                raise ValueError("missing mcp argument")
            payload = {key: data[key] for key in _properties if key in data}
            for ignored in ("user_id", "connector_id", "connector"):
                payload.pop(ignored, None)

            async def call() -> str:
                connection = await self._require_connection(_user, _connector.name)
                if _remote not in connection.selected_tools:
                    raise PermissionError("mcp user denied")
                message = await self._rpc(
                    _user,
                    _connector,
                    "tools/call",
                    {"name": _remote, "arguments": payload},
                )
                result = message.get("result")
                if not isinstance(result, dict):
                    scope = CURRENT_MCP_SCOPE.get()
                    logger.warning(
                        "mcp remote tool returned invalid result request_id=%s connector=%s tool=%s",
                        scope.request_id if scope is not None else "-",
                        _connector.name,
                        _remote,
                    )
                    raise RuntimeError("mcp tool failed")
                if result.get("isError") is True:
                    scope = CURRENT_MCP_SCOPE.get()
                    logger.warning(
                        "mcp remote tool returned error request_id=%s connector=%s tool=%s category=%s",
                        scope.request_id if scope is not None else "-",
                        _connector.name,
                        _remote,
                        _remote_error_category(result),
                    )
                    raise RuntimeError("mcp tool failed")
                chunks: list[str] = []
                content = result.get("content")
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                            chunks.append(item["text"])
                text = "\n".join(chunks)
                return json.dumps({"untrusted": True, "text": text}, ensure_ascii=False)

            scope = CURRENT_MCP_SCOPE.get()
            if scope is not None and scope.user_id != _user:
                raise PermissionError("mcp user denied")
            if scope is None:
                return await call()
            ctx = context_for(local, timeout=self.settings.mcp_timeout_seconds, idempotent=not writable)
            if ctx is None or ctx.connector_id != _connector.name or ctx.tool_name != _remote or ctx.user_id != _user:
                raise PermissionError("mcp user denied")

            async def allowed(item: object) -> bool:
                del item
                try:
                    connection = await self.store.get_connection(_user, _connector.name)
                except Exception:
                    return False
                return connection is not None and _remote in connection.selected_tools

            return await run_mcp(ctx, allowed, call)

        return ToolSpec(
            name=local,
            description=str(tool.get("description") or _description(remote)),
            args_model=McpArgs,
            handler=handler,
            access="mutate_remote" if writable else "read_network",
            requires_network=True,
            timeout_s=self.settings.mcp_timeout_seconds,
            max_calls_per_run=5,
            idempotent=not writable,
            toolset="mcp",
            parameters=schema,
        )


def overlay_tools(settings: Settings, base_registry: ToolRegistry, extras: list[ToolSpec]) -> ToolRegistry:
    registry = ToolRegistry()
    for spec in base_registry.all_tools():
        registry._tools[spec.name] = spec
    for spec in extras:
        registry.register(spec)
    del settings
    return registry


def _remote_error_category(result: dict[str, Any]) -> str:
    """Classify MCP error text without writing page content or raw errors to logs."""
    content = result.get("content")
    fragments = [
        item["text"]
        for item in content
        if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
    ] if isinstance(content, list) else []
    message = " ".join(fragments).lower()
    if any(term in message for term in ("permission", "forbidden", "access denied", "not shared")):
        return "permission_denied"
    if any(term in message for term in ("unauthorized", "token expired", "reauthoriz")):
        return "authentication_required"
    if any(term in message for term in ("not found", "does not exist", "could not find")):
        return "resource_not_found"
    if any(term in message for term in ("rate limit", "too many requests")):
        return "rate_limited"
    if any(term in message for term in ("invalid", "required", "missing", "validation")):
        return "invalid_arguments"
    if "timeout" in message or "timed out" in message:
        return "remote_timeout"
    return "remote_tool_error"


def _resource_metadata(header: str, resource: str) -> str | None:
    match = re.search(r'resource_metadata="([^"]+)"', header)
    if match is None:
        return None
    value = match.group(1).strip()
    if not value:
        return None
    return urljoin(resource, value)


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        return ""
    return header.split(" ", 1)[1].strip()
