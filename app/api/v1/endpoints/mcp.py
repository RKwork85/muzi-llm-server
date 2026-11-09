"""用户连接目录中的远程 MCP。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.core.database.session import get_session_factory
from app.core.exceptions import LoginRequiredError, McpUnavailableError
from app.mcp.pool import production_pool
from app.mcp.service import McpService
from app.mcp.store import SqlStore

logger = logging.getLogger("muzi_llm")
router = APIRouter(prefix="/mcp", tags=["mcp"])


class ToolSelection(BaseModel):
    tools: list[str] = Field(default_factory=list, max_length=32)


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


@router.get("/connectors", summary="远程服务目录和当前用户的连接状态")
async def list_connectors(request: Request, service: McpService = Depends(required_mcp)) -> dict[str, object]:
    user_id = await service.user_id_from_request(request)
    return {"connectors": await service.list_for(user_id)}


@router.post("/connectors/{name}/connect", summary="开始授权")
async def connect(name: str, request: Request, service: McpService = Depends(required_mcp)) -> dict[str, str]:
    user_id = await service.require_user(request)
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
