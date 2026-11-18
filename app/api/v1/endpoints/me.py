"""当前登录用户信息与有效权限。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import get_authenticated_user, get_stream_user_id
from app.authorization.application.authorization_service import AuthorizationService
from app.authorization.application.visibility import visible_tools
from app.core.database.deps import DbSession
from app.core.config import get_settings
from app.models.rbac import ResourceGrant
from app.models.tables import User
from app.schemas.auth import ChangeUsernameRequest, UserOut
from app.services.auth_service import AuthService
from app.schemas.avatar import AvatarStatusOut, AvatarUpload
from app.services.recharge_events import subscribe as subscribe_recharge_events
from app.schemas.preferences import (
    BackgroundSettings,
    DEFAULT_COLOR_CHAT,
    DEFAULT_COLOR_PRIMARY,
    UserPreferencesOut,
    UserPreferencesUpdate,
)
from app.schemas.rbac import (
    CapabilitiesOut,
    MeResponse,
    ModeCapabilitiesOut,
    QuotaSummary,
    UsageListResponse,
    UsageRecordOut,
    UsageReportRequest,
)
from app.services.avatar_service import AvatarService
from app.authorization.application.audit_service import QuotaService
from app.authorization.application.usage_turns import load_usage_analysis
from app.authorization.application.chat_run_detail import load_chat_run_detail
from app.core.exceptions import ChatRunNotFoundError, InvalidRequestError
from app.schemas.chat_run import ChatRunDetailResponse
from app.schemas.usage_analysis import UsageAnalysisResponse
from app.api.v1.endpoints.mcp import optional_mcp
from app.mcp.service import McpService
from app.tools.capabilities import MCP_MODES, MODE_TOOL_NAMES
from app.authorization.application.execution_guard import bind_workspace_grant
from app.tools.plugins import visible_plugins
from app.tools.registry import ToolRegistry, build_agent_registry
from app.tools.workspace_scope import is_user_scope, scope_dirname

router = APIRouter(prefix="/me", tags=["me"])

_MODES = ("ask", "plan", "agent")


@router.get("", response_model=MeResponse, summary="当前用户信息与有效权限")
async def get_me(
    user: Annotated[User, Depends(get_authenticated_user)],
    session: DbSession,
) -> MeResponse:
    snapshot = await AuthorizationService(session).snapshot(user.id)
    return MeResponse(
        user=UserOut(
            id=user.id,
            email=user.email,
            username=user.username,
            display_name=user.display_name,
            status=user.status,
            avatar=user.avatar,
            phone=user.phone,
        ),
        roles=list(snapshot.roles),
        permissions=sorted(snapshot.permissions),
        data_scope=snapshot.data_scope,
    )


@router.get("/capabilities", response_model=CapabilitiesOut, summary="读取各模式实际可用的工具")
async def get_capabilities(
    request: Request,
    user: Annotated[User, Depends(get_authenticated_user)],
    session: DbSession,
    mcp: McpService | None = Depends(optional_mcp),
) -> CapabilitiesOut:
    """工具是否出现由服务端能力投影决定，前端不再自行推导 toolsets/grants。

    传入 X-Workspace-Key 时，agent 模式会按该工作区是否已归属来收窄写类工具。
    """
    snapshot = await AuthorizationService(session).snapshot(user.id)
    registry = _tool_registry(request)
    settings = getattr(request.app.state, "settings", None) or get_settings()
    scope = scope_dirname(
        request.headers.get("x-workspace-key") or request.query_params.get("workspace_key")
    )
    # 与真实请求保持一致：首次出示合法 scope 即登记归属，之后 agent 的写类工具才可见。
    await bind_workspace_grant(session, user.id, scope)
    owned = await _owned_workspaces(session, user.id)
    # 连接器授权是「按人」的运行时事实，不在权限位里，必须单独读。
    # 只读库不发网络请求，所以放在这个高频接口上不会拖慢页面。
    connectors = await (mcp.authorized_connectors(str(user.id)) if mcp is not None else frozenset())

    modes: dict[str, ModeCapabilitiesOut] = {}
    for mode in _MODES:
        visibility = visible_tools(
            registry,
            settings,
            mode=mode,
            permissions=snapshot.permissions,
            workspace_scope=scope,
            owned_scopes=owned,
            offered=MODE_TOOL_NAMES[mode],
            include_mcp=mode in MCP_MODES,
            mcp_user_id=str(user.id) if mode in MCP_MODES else None,
        )
        plugins = visible_plugins(
            visibility.allowed,
            permissions=snapshot.permissions,
            mode=mode,
            connectors=connectors,
        )
        modes[mode] = ModeCapabilitiesOut(
            tools=list(visibility.allowed),
            toolsets=list(visibility.toolsets(registry)),
            denied=dict(visibility.denied),
            plugins=list(plugins.allowed),
            denied_plugins=dict(plugins.denied),
        )
    return CapabilitiesOut(modes=modes)


@router.get("/quota", response_model=QuotaSummary, summary="读取我的额度剩余")
async def get_my_quota(
    user: Annotated[User, Depends(get_authenticated_user)],
    session: DbSession,
) -> QuotaSummary:
    summary = await QuotaService(session).get_quota_summary(user.id)
    return QuotaSummary(**summary)


@router.get("/events", summary="SSE：实时推送额度到账等事件")
async def stream_my_events(
    request: Request,
    user_id: Annotated[str, Depends(get_stream_user_id)],
) -> StreamingResponse:
    """登录态 SSE 长连接。充值到账后服务端主动推送，前端无需轮询或刷新。

    约定：
    - 事件名即 `type`（如 `quota.credited`），`data` 为 JSON。
    - 空闲每 15 秒发一次注释帧 `: ping` 保活，避免代理断连。
    - 客户端断开或服务重启后连接结束，由前端按退避策略重连。

    鉴权刻意用 `get_stream_user_id`（自带短事务）而不是 `get_authenticated_user`：
    后者借的是请求级数据库连接，要等响应结束才归还，而这条响应在客户端断开前
    永远不会结束——那样每条 SSE 都会长期占住一个连接池名额，几个页签就能把池子
    抽干，之后所有请求都卡在等连接上（前端表现为「页面卡死」）。
    """

    async def body() -> AsyncIterator[bytes]:
        # 首帧尽快让代理与浏览器确认握手成功，前端据此把状态切到「已连接」。
        yield b": connected\n\n"
        async for event in subscribe_recharge_events(user_id):
            # 服务重启/进程关闭时 uvicorn 会先标记连接断开，这里及时收尾，
            # 避免长连接把优雅关闭一直拖住。
            if await request.is_disconnected():
                break
            if event is None:
                yield b": ping\n\n"
                continue
            frame = f"event: {event.get('type', 'message')}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            yield frame.encode("utf-8")

    headers = {
        "Cache-Control": "no-cache",
        # 关闭 nginx 等反向代理的响应缓冲，否则事件会被攒着一起下发。
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(body(), media_type="text/event-stream", headers=headers)


@router.get("/usage", response_model=UsageListResponse, summary="读取我的额度消耗明细")
async def list_my_usage(
    user: Annotated[User, Depends(get_authenticated_user)],
    session: DbSession,
    kind: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> UsageListResponse:
    if kind not in (None, "chat", "image"):
        from app.core.exceptions import InvalidRequestError

        raise InvalidRequestError("kind 参数无效")
    rows, total, total_consumed, kind_breakdown = await QuotaService(session).list_usage_records(
        user.id, limit=limit, offset=offset, kind=kind
    )
    return UsageListResponse(
        items=[_usage_record_out(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
        total_consumed=total_consumed,
        kind_breakdown=kind_breakdown,
    )


@router.get("/usage/analysis", response_model=UsageAnalysisResponse, summary="按轮次读取额度消耗分析")
async def analyze_my_usage(
    user: Annotated[User, Depends(get_authenticated_user)],
    session: DbSession,
    range_key: str = Query(default="30d", alias="range"),
    kind: str | None = None,
    model: str | None = Query(default=None, max_length=128),
    mode: str | None = None,
    q: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
) -> UsageAnalysisResponse:
    """只读聚合。不修改额度扣减。"""
    if range_key not in {"7d", "30d", "90d", "all"}:
        raise InvalidRequestError("range 参数无效")
    if kind not in (None, "", "chat", "image"):
        raise InvalidRequestError("kind 参数无效")
    if mode not in (None, "", "ask", "plan", "agent"):
        raise InvalidRequestError("mode 参数无效")
    payload = await load_usage_analysis(
        session,
        user.id,
        range_key=range_key,
        kind=kind or None,
        model=(model or "").strip() or None,
        mode=mode or None,
        query=(q or "").strip() or None,
        limit=limit,
        offset=offset,
    )
    return UsageAnalysisResponse.model_validate(payload)


@router.get("/usage/calls", summary="读取模型调用分类")
async def list_my_usage_calls(
    user: Annotated[User, Depends(get_authenticated_user)],
    session: DbSession,
    conversation_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, object]:
    from sqlalchemy import select

    from app.models.metering import LlmUsageBreakdown, LlmUsageRecord

    stmt = select(LlmUsageRecord).where(LlmUsageRecord.user_id == user.id)
    if conversation_id:
        stmt = stmt.where(LlmUsageRecord.conversation_id == conversation_id)
    stmt = stmt.order_by(LlmUsageRecord.created_at.desc()).limit(limit)
    rows = list(await session.scalars(stmt))
    ids = [row.id for row in rows]
    parts: dict[object, list[dict[str, object]]] = {row.id: [] for row in rows}
    if ids:
        detail_rows = await session.scalars(
            select(LlmUsageBreakdown).where(LlmUsageBreakdown.usage_record_id.in_(ids))
        )
        for item in detail_rows:
            parts.setdefault(item.usage_record_id, []).append(
                {
                    "direction": item.direction,
                    "category": item.category,
                    "token_count": item.token_count,
                    "attribution_method": item.attribution_method,
                }
            )
    return {
        "items": [
            {
                "id": str(row.id),
                "conversation_id": row.conversation_id,
                "model": row.model,
                "runtime_mode": row.runtime_mode,
                "request_id": row.request_id,
                "usage_source": row.usage_source,
                "status": row.status,
                "input_tokens": row.input_tokens,
                "output_tokens": row.output_tokens,
                "reasoning_tokens": row.reasoning_tokens,
                "cache_read_tokens": row.cache_read_tokens,
                "cache_write_tokens": row.cache_write_tokens,
                "total_tokens": row.total_tokens,
                "actual_cost": str(row.actual_cost) if row.actual_cost is not None else None,
                "currency": row.currency,
                "breakdown": parts.get(row.id, []),
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
    }


@router.post("/usage/report", response_model=QuotaSummary, summary="上报一次额度消耗（图片生成等）")
async def report_my_usage(
    body: UsageReportRequest,
    user: Annotated[User, Depends(get_authenticated_user)],
    session: DbSession,
) -> QuotaSummary:
    _ = body
    summary = await QuotaService(session).get_quota_summary(user.id)
    return QuotaSummary(**summary)


def _tool_registry(request: Request) -> ToolRegistry:
    service = getattr(request.app.state, "chat_service", None)
    tools = getattr(service, "ask_tools", None)
    registry = getattr(tools, "registry", None)
    return registry if registry is not None else build_agent_registry()


async def _owned_workspaces(session, user_id) -> frozenset[str]:
    rows = await session.scalars(
        select(ResourceGrant.resource_id).where(
            ResourceGrant.user_id == user_id,
            ResourceGrant.resource_type == "workspace",
            ResourceGrant.grant_type == "allow",
            ResourceGrant.revoked_at.is_(None),
        )
    )
    return frozenset(str(item) for item in rows if is_user_scope(str(item)))


def _usage_record_out(record) -> UsageRecordOut:
    ctx = record.context_json or {}
    # 兼容历史数据：旧版以 total_tokens 作为 metric，统一归为「对话」。
    kind = record.metric or "chat"
    if kind not in ("chat", "image"):
        kind = "chat"
    return UsageRecordOut(
        id=record.id,
        kind=kind,
        amount=record.amount,
        model=ctx.get("model"),
        conversation_id=ctx.get("conversation_id"),
        conversation_title=ctx.get("conversation_title"),
        prompt_tokens=ctx.get("prompt_tokens", 0),
        completion_tokens=ctx.get("completion_tokens", 0),
        image_count=ctx.get("image_count"),
        purpose=ctx.get("purpose") or ("summary" if record.source == "summary" else None),
        created_at=record.created_at,
    )


def _preferences_out(user: User) -> UserPreferencesOut:
    """从用户偏好 JSONB 读出背景设置，缺失字段回退默认值。"""
    background = (user.preferences or {}).get("background") or {}
    return UserPreferencesOut(
        background=BackgroundSettings(
            color_chat=background.get("color_chat", DEFAULT_COLOR_CHAT),
            color_primary=background.get("color_primary", DEFAULT_COLOR_PRIMARY),
            image_url=background.get("image_url"),
            blur=background.get("blur", 0),
        )
    )


@router.get("/preferences", response_model=UserPreferencesOut, summary="读取我的通用设置")
async def get_preferences(
    user: Annotated[User, Depends(get_authenticated_user)],
) -> UserPreferencesOut:
    return _preferences_out(user)


@router.put("/preferences", response_model=UserPreferencesOut, summary="更新我的通用设置")
async def update_preferences(
    body: UserPreferencesUpdate,
    user: Annotated[User, Depends(get_authenticated_user)],
    session: DbSession,
) -> UserPreferencesOut:
    preferences = dict(user.preferences or {})
    if body.background is not None:
        preferences["background"] = {
            "color_chat": body.background.color_chat,
            "color_primary": body.background.color_primary,
            "image_url": body.background.image_url,
            "blur": body.background.blur,
        }
    user.preferences = preferences
    await session.flush()
    return _preferences_out(user)


@router.get("/chat-runs/{run_id}", response_model=ChatRunDetailResponse, summary="读取我的一轮对话运行详情")
async def get_my_chat_run(
    run_id: str,
    user: Annotated[User, Depends(get_authenticated_user)],
    session: DbSession,
) -> ChatRunDetailResponse:
    detail = await load_chat_run_detail(session, user.id, run_id)
    if detail is None:
        raise ChatRunNotFoundError()
    return detail


@router.get("/avatar/status", response_model=AvatarStatusOut, summary="读取头像与剩余修改次数")
async def get_avatar_status(
    user: Annotated[User, Depends(get_authenticated_user)],
    session: DbSession,
) -> AvatarStatusOut:
    service = AvatarService(session)
    return AvatarStatusOut(
        avatar=user.avatar,
        remaining=await service.remaining(user),
    )


@router.put("/avatar", response_model=AvatarStatusOut, summary="上传并保存裁剪后的头像")
async def update_avatar(
    body: AvatarUpload,
    user: Annotated[User, Depends(get_authenticated_user)],
    session: DbSession,
) -> AvatarStatusOut:
    service = AvatarService(session)
    await service.update_avatar(user, body.data_url)
    await session.flush()
    return AvatarStatusOut(
        avatar=user.avatar,
        remaining=await service.remaining(user),
    )


@router.put("/username", response_model=UserOut, summary="修改我的用户名")
async def update_username(
    body: ChangeUsernameRequest,
    user: Annotated[User, Depends(get_authenticated_user)],
    session: DbSession,
) -> UserOut:
    """修改用户名后回传最新的用户信息，前端据此同步本地 store。"""
    await AuthService(session).change_username(user, body.username)
    return UserOut(
        id=user.id,
        email=user.email,
        username=user.username,
        display_name=user.display_name,
        status=user.status,
        avatar=user.avatar,
        phone=user.phone,
    )
