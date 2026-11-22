"""用户连接目录中的远程 MCP。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.core.database.session import get_session_factory
from app.core.exceptions import InvalidRequestError, LoginRequiredError, McpUnavailableError
from app.authorization.application.authorization_service import AuthorizationService
from app.mcp.keys import KeyConfigurationError
from app.mcp.pool import production_pool
from app.mcp.service import McpService
from app.mcp.store import SqlStore

logger = logging.getLogger("muzi_llm")
router = APIRouter(prefix="/mcp", tags=["mcp"])


class ToolSelection(BaseModel):
    tools: list[str] = Field(default_factory=list, max_length=32)


class BridgeCredentials(BaseModel):
    """多租户 bridge 凭据。按连接器只填对应字段，其余忽略。"""

    corp_id: str = ""
    agent_id: str = ""
    secret: str = ""
    app_id: str = ""
    app_secret: str = ""


class CommunityInstall(BaseModel):
    """社区远程连接器安装请求。字段来自官方 MCP Registry 的 remote 条目。"""

    qualified_name: str
    transport: str
    url: str
    display_name: str = ""
    description: str = ""
    icon_url: str = ""
    website_url: str = ""
    auth_headers: str = ""


def _validate_bridge_credentials(name: str, body: BridgeCredentials) -> dict[str, str]:
    if name == "wecom":
        cred = {
            "corp_id": body.corp_id.strip(),
            "agent_id": body.agent_id.strip(),
            "secret": body.secret.strip(),
        }
        if not all(cred.values()):
            raise InvalidRequestError("corp_id、agent_id、secret 不能为空")
        return cred
    if name == "feishu":
        cred = {"app_id": body.app_id.strip(), "app_secret": body.app_secret.strip()}
        if not all(cred.values()):
            raise InvalidRequestError("app_id、app_secret 不能为空")
        return cred
    raise InvalidRequestError("该连接器不支持凭据配置")


@asynccontextmanager
async def mcp_context(request: Request, *, required: bool) -> AsyncIterator[McpService | None]:
    current = getattr(request.app.state, "mcp_service", None)
    if isinstance(current, McpService):
        yield current
        return
    settings = getattr(request.app.state, "settings", None)
    try:
        factory = get_session_factory()
    except RuntimeError:
        factory = None
    if settings is None or factory is None:
        if required:
            raise McpUnavailableError()
        yield None
        return
    pool = getattr(request.app.state, "mcp_pool", None)
    owned = pool is None
    async with factory() as session:
        service = McpService(settings, SqlStore(session), pool or production_pool(settings))
        try:
            yield service
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            if owned:
                await service.aclose()


async def optional_mcp(request: Request) -> AsyncIterator[McpService | None]:
    async with mcp_context(request, required=False) as service:
        yield service


async def required_mcp(request: Request) -> AsyncIterator[McpService]:
    async with mcp_context(request, required=True) as service:
        if service is None:
            raise McpUnavailableError()
        yield service


async def _require_connect(user_id: str) -> None:
    """建立连接 / 写入凭据需要 `mcp.connect`。

    后台「连接器」页面里这个开关如果只在前端生效，就等于装饰：拿到 token
    直接调接口照样能连。因此写连接的动作统一在这里校验一次，省得每个
    端点各写一遍、漏一个就漏一条路。
    """
    factory = get_session_factory()
    session = factory()
    try:
        await AuthorizationService(session).require_permission(user_id, "mcp.connect")
    finally:
        await session.close()


@router.get("/connectors", summary="远程服务目录和当前用户的连接状态")
async def list_connectors(request: Request, service: McpService = Depends(required_mcp)) -> dict[str, object]:
    user_id = await service.user_id_from_request(request)
    return {"connectors": await service.list_for(user_id)}


@router.post("/connectors/{name}/connect", summary="开始授权")
async def connect(name: str, request: Request, service: McpService = Depends(required_mcp)) -> dict[str, str]:
    user_id = await service.require_user(request)
    await _require_connect(user_id)
    return {"authorization_url": await service.begin(user_id, name)}


@router.get("/oauth/callback", summary="完成授权并回到页面")
async def callback(
    request: Request,
    code: str = "",
    state: str = "",
    iss: str = "",
    service: McpService = Depends(required_mcp),
) -> RedirectResponse:
    try:
        name = await service.finish(code, state, iss)
    except Exception:
        logger.info("mcp oauth callback failed")
        name = ""
    # 授权完成后回到插件页，由页面读取 connected 并拉取工具。
    target = service.settings.mcp_frontend_base_url.rstrip("/") + "/apps"
    if name:
        target = f"{target}?connected={name}"
    else:
        target = f"{target}?connected=failed"
    return RedirectResponse(target, status_code=302)


@router.get("/connectors/{name}/tools", summary="列出当前用户可见的远端工具")
async def list_tools(name: str, request: Request, service: McpService = Depends(required_mcp)) -> dict[str, object]:
    user_id = await service.require_user(request)
    return {"tools": await service.discover(user_id, name)}


@router.put("/connectors/{name}/tools", summary="保存当前用户勾选的工具")
async def select_tools(
    name: str,
    body: ToolSelection,
    request: Request,
    service: McpService = Depends(required_mcp),
) -> dict[str, object]:
    user_id = await service.require_user(request)
    return {"tools": await service.select(user_id, name, body.tools)}


@router.delete("/connectors/{name}", summary="断开当前用户的连接")
async def disconnect(name: str, request: Request, service: McpService = Depends(required_mcp)) -> dict[str, str]:
    user_id = await service.require_user(request)
    await service.disconnect(user_id, name)
    return {"status": "disconnected"}


class AccessToken(BaseModel):
    """用户自带的连接凭据。token 为 Bearer 令牌，headers 为自定义认证头。"""

    token: str = Field(default="", max_length=4096)
    headers: dict[str, str] = Field(default_factory=dict)


@router.put("/connectors/{name}/auth", summary="用访问令牌/认证头连接（无标准 OAuth 的服务）")
async def put_auth(
    name: str,
    body: AccessToken,
    request: Request,
    service: McpService = Depends(required_mcp),
) -> dict[str, object]:
    """社区目录里只认 `Authorization: Bearer <token>` 或 `X-API-Key` 这类自定义头的远程 MCP 走这条路。"""
    user_id = await service.require_user(request)
    await _require_connect(user_id)
    tools = await service.connect_with_credentials(user_id, name, token=body.token, headers=body.headers)
    return {"status": "connected", "connector": name, "selected_tools": tools}


@router.put("/connectors/{name}/token", summary="用访问令牌连接（兼容旧入口）")
async def put_token(
    name: str,
    body: AccessToken,
    request: Request,
    service: McpService = Depends(required_mcp),
) -> dict[str, object]:
    """等价于 `/auth` 的 `token` 字段，保留给旧调用方。"""
    user_id = await service.require_user(request)
    await _require_connect(user_id)
    tools = await service.connect_with_token(user_id, name, body.token)
    return {"status": "connected", "connector": name, "selected_tools": tools}


@router.get("/connectors/{name}/credentials", summary="查询 bridge 凭据是否已配置")
async def get_credentials(name: str, request: Request, service: McpService = Depends(required_mcp)) -> dict[str, object]:
    user_id = await service.require_user(request)
    return await service.bridge_credentials_status(user_id, name)


@router.put("/connectors/{name}/credentials", summary="保存当前用户的 bridge 凭据并建立连接")
async def put_credentials(
    name: str,
    body: BridgeCredentials,
    request: Request,
    service: McpService = Depends(required_mcp),
) -> dict[str, str]:
    user_id = await service.require_user(request)
    await _require_connect(user_id)
    credentials = _validate_bridge_credentials(name, body)
    try:
        await service.configure_bridge(user_id, name, credentials)
    except KeyConfigurationError:
        raise McpUnavailableError("连接服务密钥未配置，暂无法保存凭据") from None
    return {"status": "connected", "connector": name}


@router.get("/community/search", summary="搜索社区目录（可安装 / 全部 + 热度排序）")
async def community_search(
    q: str = "",
    sort: str = "stars",
    limit: int = 40,
    page: int = 1,
    installable_only: bool = False,
    service: McpService = Depends(required_mcp),
) -> dict[str, object]:
    return await service.search_community(q, sort, limit, page, installable_only)


@router.get("/community/connectors", summary="已安装的社区连接器目录")
async def community_list(request: Request, service: McpService = Depends(required_mcp)) -> dict[str, object]:
    user_id = await service.require_user(request)
    return {"connectors": await service.community_connectors(user_id)}


@router.post("/community/connectors", summary="接入社区远程连接器并开始授权")
async def community_install(
    body: CommunityInstall,
    request: Request,
    service: McpService = Depends(required_mcp),
) -> dict[str, object]:
    """接入目录项并立刻给出「下一步」。

    安装本身只是往平台目录里加一条记录，不产生任何可用能力；真正的「能用」发生在
    用户完成授权之后。所以两步必须绑在一起，并把下一步告诉前端：

    - ``next=oauth``：跳转 ``authorization_url``。
    - ``next=token``：该服务没有标准 OAuth，前端弹窗让用户粘贴访问令牌。

    授权无法发起时不留下「已安装但永远连不上」的目录项：本次新建的目录项回滚。
    """
    user_id = await service.require_user(request)
    await _require_connect(user_id)
    if not body.qualified_name.strip() or not body.url.strip():
        raise InvalidRequestError("qualified_name 与 url 不能为空")
    installed = await service.install_community(
        body.qualified_name.strip(),
        body.transport,
        body.url.strip(),
        body.display_name,
        body.description,
        body.icon_url,
        body.website_url,
        body.auth_headers,
    )
    name = str(installed["name"])
    try:
        authorization_url = await service.begin(user_id, name)
    except McpUnavailableError:
        # 官方目录里大量服务只认自带的 Bearer Token。保留目录项，改走令牌连接，
        # 而不是把用户丢在一张「已安装但未连接」的死卡片上。
        return {**installed, "next": "token"}
    except Exception:
        if installed.get("status") == "created":
            await service.uninstall_community(name)
        raise
    return {**installed, "next": "oauth", "authorization_url": authorization_url}


@router.delete("/community/connectors/{name}", summary="卸载社区远程连接器")
async def community_uninstall(
    name: str,
    request: Request,
    service: McpService = Depends(required_mcp),
) -> dict[str, str]:
    user_id = await service.require_user(request)
    await service.uninstall_community(name, user_id=user_id)
    return {"status": "uninstalled"}
