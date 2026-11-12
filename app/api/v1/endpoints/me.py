"""当前登录用户信息与有效权限。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_authenticated_user
from app.authorization.application.authorization_service import AuthorizationService
from app.core.database.deps import DbSession
from app.models.tables import User
from app.schemas.auth import UserOut
from app.schemas.avatar import AvatarStatusOut, AvatarUpload
from app.schemas.preferences import (
    BackgroundSettings,
    DEFAULT_COLOR_CHAT,
    DEFAULT_COLOR_PRIMARY,
    UserPreferencesOut,
    UserPreferencesUpdate,
)
from app.schemas.rbac import MeResponse, QuotaSummary, UsageListResponse, UsageRecordOut, UsageReportRequest
from app.services.avatar_service import AvatarService
from app.authorization.application.audit_service import QuotaService
from app.authorization.application.usage_turns import load_usage_analysis
from app.core.exceptions import InvalidRequestError
from app.schemas.usage_analysis import UsageAnalysisResponse

router = APIRouter(prefix="/me", tags=["me"])


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


@router.get("/quota", response_model=QuotaSummary, summary="读取我的额度剩余")
async def get_my_quota(
    user: Annotated[User, Depends(get_authenticated_user)],
    session: DbSession,
) -> QuotaSummary:
    summary = await QuotaService(session).get_quota_summary(user.id)
    return QuotaSummary(**summary)


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
        }
    user.preferences = preferences
    await session.flush()
    return _preferences_out(user)


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
