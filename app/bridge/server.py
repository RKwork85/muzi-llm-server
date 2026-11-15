"""自建 MCP 桥接服务端：简化 OAuth 2.0 AS + MCP JSON-RPC（Streamable HTTP）。

企业微信/飞书是多租户凭据：每个用户在平台侧填写自己的凭据（前端表单），
平台用 KeyRing 加密后签发一个绑定 user_id 且内嵌加密凭据的 MCP 令牌。
桥接端无需访问数据库：从令牌里解开凭据即可调用企微/飞书 OpenAPI。

MCP 客户端（app/mcp/service.py）仍走标准 OAuth 元数据 / PKCE / token 交换流程，
但「确认连接」由「保存凭据」取代，token 直接由平台 `issue_bridge_tokens` 签发。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.bridge import feishu, wecom
from app.core.config import Settings
from app.mcp.keys import KeyConfigurationError, KeyRing

router = APIRouter()

CONNECTOR_NAMES = ("wecom", "feishu")
_MODULES = {"wecom": wecom, "feishu": feishu}

ACCESS_TTL = 7200
REFRESH_TTL = 30 * 86400
CODE_TTL = 600

# 进程内授权码表：code -> {name, redirect_uri, exp}
_codes: dict[str, dict[str, Any]] = {}


def _origin(request: Request) -> str:
    return f"{request.url.scheme}://{request.url.netloc}"


def _secret(settings: Settings) -> bytes:
    key = settings.mcp_token_key.strip()
    if not key:
        key = "muzi-bridge-insecure-key"  # 仅本地开发回退
    return hashlib.sha256(key.encode("utf-8")).digest()


def _sign(settings: Settings, payload: str) -> str:
    return hmac.new(_secret(settings), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _issue_token(settings: Settings, name: str, ttl: int, kind: str, user_id: str, cred_sealed: str) -> str:
    payload = json.dumps(
        {
            "connector": name,
            "user": user_id,
            "kind": kind,
            "cred": cred_sealed,
            "exp": int(time.time()) + ttl,
        },
        separators=(",", ":"),
    )
    body = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{body}.{_sign(settings, body)}"


def _verify_token(settings: Settings, token: str, name: str) -> dict[str, Any] | None:
    try:
        body, signature = token.rsplit(".", 1)
        if not hmac.compare_digest(signature, _sign(settings, body)):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body.encode("ascii") + b"=" * (-len(body) % 4)))
    except (ValueError, KeyError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("connector") != name:
        return None
    if int(payload.get("exp", 0)) <= int(time.time()):
        return None
    return payload


def issue_bridge_tokens(settings: Settings, user_id: str, name: str, cred_sealed: str) -> dict[str, Any]:
    """平台在保存凭据后签发绑定 user 且内嵌凭据的 MCP 令牌。"""
    return {
        "access_token": _issue_token(settings, name, ACCESS_TTL, "access", user_id, cred_sealed),
        "refresh_token": _issue_token(settings, name, REFRESH_TTL, "refresh", user_id, cred_sealed),
        "expires_in": ACCESS_TTL,
        "token_type": "Bearer",
    }


def _decrypt_credentials(settings: Settings, cred_sealed: Any, user_id: str, name: str) -> dict[str, Any]:
    """从令牌中解开凭据。解不开或无效时返回空 dict（视为未配置）。"""
    if not isinstance(cred_sealed, str) or not cred_sealed or not user_id:
        return {}
    try:
        plain = KeyRing(settings).decrypt(
            cred_sealed,
            user_id=user_id,
            connector_id=name,
            credential_type="bridge_credentials",
        )
        data = json.loads(plain)
    except (KeyConfigurationError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _module(name: str):
    return _MODULES[name]


# --------------------------------------------------------------------------- #
# OAuth 2.0 授权服务器（元数据供 MCP 客户端发现；连接由「保存凭据」完成）
# --------------------------------------------------------------------------- #

@router.get("/.well-known/oauth-protected-resource")
async def protected_resource(request: Request) -> dict[str, Any]:
    origin = _origin(request)
    return {"authorization_servers": [f"{origin}/bridge/{name}" for name in CONNECTOR_NAMES]}


@router.get("/.well-known/oauth-authorization-server/bridge/{name}")
async def authorization_metadata(request: Request, name: str) -> dict[str, Any]:
    if name not in CONNECTOR_NAMES:
        return JSONResponse({"error": "invalid_connector"}, status_code=404)
    origin = _origin(request)
    base = f"{origin}/bridge/{name}"
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "registration_endpoint": f"{base}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        "authorization_response_iss_parameter_supported": False,
    }


@router.post("/bridge/{name}/register")
async def register_client(name: str) -> dict[str, Any]:
    if name not in CONNECTOR_NAMES:
        return JSONResponse({"error": "invalid_connector"}, status_code=404)
    return {"client_id": f"bridge-{name}", "token_endpoint_auth_method": "none"}


@router.get("/bridge/{name}/authorize")
async def authorize(name: str, redirect_uri: str = "", state: str = "") -> RedirectResponse:
    if name not in CONNECTOR_NAMES:
        return JSONResponse({"error": "invalid_connector"}, status_code=404)
    if not redirect_uri:
        return JSONResponse({"error": "missing_redirect_uri"}, status_code=400)
    code = secrets.token_urlsafe(24)
    _codes[code] = {"name": name, "redirect_uri": redirect_uri, "exp": time.time() + CODE_TTL}
    sep = "&" if "?" in redirect_uri else "?"
    location = f"{redirect_uri}{sep}code={code}"
    if state:
        location += f"&state={state}"
    return RedirectResponse(location, status_code=302)


@router.post("/bridge/{name}/token")
async def token(request: Request, name: str) -> JSONResponse:
    if name not in CONNECTOR_NAMES:
        return JSONResponse({"error": "invalid_connector"}, status_code=404)
    settings: Settings = request.app.state.settings
    form = await request.form()
    grant_type = str(form.get("grant_type", ""))
    if grant_type == "refresh_token":
        refresh = str(form.get("refresh_token", ""))
        payload = _verify_token(settings, refresh, name)
        if payload is None:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        return _token_response(settings, name, payload.get("user", ""), payload.get("cred", ""))
    if grant_type == "authorization_code":
        # 兼容旧的「确认连接」流程：无凭据，签发空凭据令牌（tools 为空）。
        code = str(form.get("code", ""))
        redirect_uri = str(form.get("redirect_uri", ""))
        record = _codes.pop(code, None)
        if record is None or record["exp"] < time.time() or record["redirect_uri"] != redirect_uri or record["name"] != name:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        return _token_response(settings, name, "", "")
    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)


def _token_response(settings: Settings, name: str, user_id: str, cred_sealed: str) -> JSONResponse:
    return JSONResponse(issue_bridge_tokens(settings, user_id, name, cred_sealed))


# --------------------------------------------------------------------------- #
# MCP JSON-RPC（Streamable HTTP）
# --------------------------------------------------------------------------- #

@router.post("/bridge/{name}")
async def mcp_endpoint(request: Request, name: str) -> JSONResponse:
    if name not in CONNECTOR_NAMES:
        return JSONResponse({"error": "invalid_connector"}, status_code=404)
    settings: Settings = request.app.state.settings
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return _rpc(None, error={"code": -32001, "message": "Unauthorized"})
    payload = _verify_token(settings, auth.split(" ", 1)[1].strip(), name)
    if payload is None:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "Unauthorized"}},
            status_code=401,
        )
    user_id = str(payload.get("user", ""))
    credentials = _decrypt_credentials(settings, payload.get("cred"), user_id, name)
    try:
        message = await request.json()
    except ValueError:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
            status_code=400,
        )
    if not isinstance(message, dict):
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}},
            status_code=400,
        )
    method = message.get("method")
    message_id = message.get("id")
    if method == "initialize":
        result = {
            "protocolVersion": "2025-11-25",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": f"muzi-bridge-{name}", "version": "1"},
        }
        return _rpc(message_id, result=result)
    if method == "notifications/initialized":
        return JSONResponse({"jsonrpc": "2.0"}, status_code=202)
    if method == "tools/list":
        return _rpc(message_id, result={"tools": _tools_for(name, credentials)})
    if method == "tools/call":
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        tool_name = str(params.get("name", ""))
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        result = await _module(name).call_tool(user_id, credentials, settings.mcp_timeout_seconds, tool_name, arguments)
        return _rpc(message_id, result=result)
    return _rpc(message_id, error={"code": -32601, "message": "Method not found"})


def _tools_for(name: str, credentials: dict[str, Any]) -> list[dict[str, Any]]:
    if not _module(name).configured(credentials):
        return []
    return _module(name).tools()


def _rpc(message_id: Any, *, result: Any = None, error: Any = None) -> JSONResponse:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": message_id}
    if error is not None:
        body["error"] = error
    else:
        body["result"] = result
    return JSONResponse(body)
