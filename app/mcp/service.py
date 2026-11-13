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
from app.core.exceptions import ConnectorNotFoundError, LoginRequiredError, McpUnavailableError
from app.mcp.catalog import Connector, load_catalog
from app.mcp.crypto import TokenCipher
from app.mcp.execution import CURRENT_MCP_SCOPE, context_for, run_mcp
from app.mcp.http_client import release_hub, shared_hub
from app.mcp.keys import KeyConfigurationError, KeyRing
from app.mcp.observe import metrics
from app.mcp.oauth_guard import OAuthFlowError, issuers_equal, metadata_url_for_issuer, screen_endpoint, trusted_issuer
from app.mcp.protocol import MAX_TOOL_PAGES, ProtocolError, parse_tools_page, tools_list_params
from app.mcp.pool import ClientPool, pool_from_template
from app.mcp.store import ClientRecord, ConnectionRecord, MemoryStore, SqlStore, StateRecord
from app.models.mixins import utcnow
from app.tools.mcp_client import McpArgs, _description, _schema
from app.tools.registry import ToolRegistry, ToolSpec

logger = logging.getLogger("muzi_llm")
_WRITE = re.compile(r"(create|update|delete|insert|remove|write|append|patch|move|trash|destroy)", re.I)
_REMOTE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_LOCAL = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
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
        self.catalog = catalog if catalog is not None else load_catalog(allow_insecure=settings.mcp_allow_insecure)
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
            raise LoginRequiredError() from None

    def _reveal(self, sealed: str, *, user_id: str, connector_id: str, credential_type: str) -> str:
        if sealed.startswith("v2."):
            return self.keys().decrypt(sealed, user_id=user_id, connector_id=connector_id, credential_type=credential_type)
        return self.cipher().decrypt(sealed)

    def connector(self, name: str) -> Connector:
        for item in self.catalog:
            if item.name == name:
                return item
        raise ConnectorNotFoundError()

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
        rows: list[dict[str, Any]] = []
        for item in self.catalog:
            connection = connections.get(item.name)
            rows.append(
                {
                    "name": item.name,
                    "display_name": item.display_name,
                    "transport": item.transport,
                    "status": connection.status if connection else "disconnected",
                    "selected_tools": list(connection.selected_tools) if connection else [],
                    "allow_write": item.allow_write,
                }
            )
        return rows

    async def begin(self, user_id: str, name: str) -> str:
        connector = self.connector(name)
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
            raise LoginRequiredError()
        saved = await self.store.pop_state(hashlib.sha256(state.encode("utf-8")).hexdigest())
        if saved is None:
            raise LoginRequiredError()
        connector = self.connector(saved.connector_name)
        bound = self._open_verifier(saved.code_verifier_encrypted, saved.user_id, saved.connector_name)
        if not self._iss_matches(bound, iss):
            logger.info("mcp oauth rejected connector=%s reason=issuer_mismatch", connector.name)
            raise LoginRequiredError()
        client = await self.store.get_client(connector.name)
        if client is None:
            raise LoginRequiredError()
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
        token = await self._token(saved.user_id, connector.name, endpoint, form)
        await self._save_token(
            saved.user_id,
            connector.name,
            token,
            previous=None,
            binding={"issuer": bound["issuer"], "token_endpoint": endpoint},
        )
        await self._drop_client(saved.user_id, connector.name)
        return connector.name

    async def discover(self, user_id: str, name: str) -> list[dict[str, Any]]:
        connector = self.connector(name)
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
        self.connector(name)
        await self.store.delete_connection(user_id, name)
        await self._drop_client(user_id, name)

    async def tool_specs(self, user_id: str) -> list[ToolSpec]:
        specs: list[ToolSpec] = []
        for connection in await self.store.list_connections(user_id):
            if connection.status != "connected":
                continue
            try:
                connector = self.connector(connection.connector_name)
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
            )
        except PermissionError:
            await self._mark_reauth(connection)
            raise LoginRequiredError() from None

    async def _access_token(self, connection: ConnectionRecord, connector: Connector) -> str:
        opened = self._open_access(connection.access_token_encrypted, connection.user_id, connector.name)
        expired = connection.expires_at is not None and connection.expires_at <= utcnow() + timedelta(seconds=30)
        if not expired and opened["token"]:
            return opened["token"]
        if not connection.refresh_token_encrypted:
            await self._mark_reauth(connection)
            raise LoginRequiredError()
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
            await self._mark_reauth(connection)
            raise LoginRequiredError()
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
            await self._mark_reauth(connection)
            raise LoginRequiredError() from None
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
            raise LoginRequiredError()
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

    async def _mark_reauth(self, connection: ConnectionRecord) -> None:
        connection.status = "needs_reauth"
        await self.store.save_connection(connection)

    async def _require_connection(self, user_id: str, name: str) -> ConnectionRecord:
        connection = await self.store.get_connection(user_id, name)
        if connection is None or connection.status != "connected":
            raise LoginRequiredError()
        return connection

    async def _metadata(self, user_id: str, connector: Connector) -> dict[str, str]:
        try:
            anchor = trusted_issuer(connector.url, connector.issuer)
            await self._screen(user_id, connector.name, connector.url)
        except OAuthFlowError as exc:
            logger.info("mcp oauth rejected connector=%s reason=%s", connector.name, exc.reason)
            raise LoginRequiredError() from None
        protected = await self._protected_metadata(user_id, connector)
        servers = protected.get("authorization_servers") if isinstance(protected, dict) else None
        announced = [item for item in servers if isinstance(item, str)] if isinstance(servers, list) else []
        if not any(issuers_equal(anchor, item) for item in announced):
            logger.info("mcp oauth rejected connector=%s reason=issuer_unlisted", connector.name)
            raise LoginRequiredError()
        try:
            document_url = await self._screen(user_id, connector.name, metadata_url_for_issuer(anchor))
            document = await self._json(user_id, connector.name, document_url)
        except OAuthFlowError as exc:
            logger.info("mcp oauth rejected connector=%s reason=%s", connector.name, exc.reason)
            raise LoginRequiredError() from None
        if not isinstance(document, dict) or not issuers_equal(anchor, str(document.get("issuer") or "")):
            logger.info("mcp oauth rejected connector=%s reason=issuer_mismatch", connector.name)
            raise LoginRequiredError()
        authorization = document.get("authorization_endpoint")
        token_endpoint = document.get("token_endpoint")
        if not isinstance(authorization, str) or not isinstance(token_endpoint, str):
            raise LoginRequiredError()
        try:
            authorization = await self._screen(user_id, connector.name, authorization)
            token_endpoint = await self._screen(user_id, connector.name, token_endpoint)
            registration = document.get("registration_endpoint")
            if isinstance(registration, str) and registration:
                registration = await self._screen(user_id, connector.name, registration)
            else:
                registration = ""
        except OAuthFlowError as exc:
            logger.info("mcp oauth rejected connector=%s reason=%s", connector.name, exc.reason)
            raise LoginRequiredError() from None
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
        if not metadata["registration_endpoint"]:
            raise LoginRequiredError()
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
        body = response.json()
        client_id = body.get("client_id") if isinstance(body, dict) else None
        if not isinstance(client_id, str) or not client_id:
            raise LoginRequiredError()
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
            raise LoginRequiredError()
        return body

    async def _protected_metadata(self, user_id: str, connector: Connector) -> dict[str, Any]:
        pointed = await self._pointed_metadata(user_id, connector)
        if pointed:
            try:
                screened = await self._screen(user_id, connector.name, pointed)
                return await self._json(user_id, connector.name, screened)
            except (LoginRequiredError, OAuthFlowError):
                pass
        parsed = urlparse(connector.url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        fallback = await self._screen(user_id, connector.name, f"{origin}/.well-known/oauth-protected-resource")
        return await self._json(user_id, connector.name, fallback)

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
            raise LoginRequiredError() from None
        self._reject_redirect(name, response)
        if response.status_code >= 400:
            raise LoginRequiredError()
        return response

    async def _post(self, user_id: str, name: str, url: str, **kwargs: object) -> httpx.Response:
        try:
            response = await self.pool.get(user_id, name).post(url, follow_redirects=False, **kwargs)
        except OAuthFlowError as exc:
            logger.info("mcp oauth rejected connector=%s reason=%s", name, exc.reason)
            raise LoginRequiredError() from None
        self._reject_redirect(name, response)
        if response.status_code >= 400:
            raise LoginRequiredError()
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
            raise LoginRequiredError()

    def _open_verifier(self, sealed: str, user_id: str, connector_id: str) -> dict[str, str]:
        try:
            data = json.loads(self._reveal(sealed, user_id=user_id, connector_id=connector_id, credential_type="pkce"))
        except (ValueError, json.JSONDecodeError, KeyConfigurationError):
            raise LoginRequiredError() from None
        if not isinstance(data, dict) or data.get("v") != 1:
            raise LoginRequiredError()
        verifier = data.get("verifier")
        issuer = data.get("issuer")
        endpoint = data.get("token_endpoint")
        if not isinstance(verifier, str) or not isinstance(issuer, str) or not isinstance(endpoint, str):
            raise LoginRequiredError()
        return {
            "verifier": verifier,
            "issuer": issuer,
            "token_endpoint": endpoint,
            "iss_required": "1" if data.get("iss_required") else "",
        }

    def _open_access(self, sealed: str, user_id: str, connector_id: str) -> dict[str, str]:
        try:
            raw = self._reveal(sealed, user_id=user_id, connector_id=connector_id, credential_type="access_token")
        except (ValueError, KeyConfigurationError):
            return {"token": "", "issuer": "", "token_endpoint": ""}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {"token": raw, "issuer": "", "token_endpoint": ""}
        if not isinstance(data, dict) or data.get("v") != 1:
            return {"token": raw, "issuer": "", "token_endpoint": ""}
        token = data.get("token")
        issuer = data.get("issuer")
        endpoint = data.get("token_endpoint")
        return {
            "token": token if isinstance(token, str) else "",
            "issuer": issuer if isinstance(issuer, str) else "",
            "token_endpoint": endpoint if isinstance(endpoint, str) else "",
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
