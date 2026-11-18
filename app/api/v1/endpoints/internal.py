"""内部服务间接口。

仅供独立支付服务等内部进程调用，走共享密钥鉴权，不走登录态。
密钥必须配置（INTERNAL_API_TOKEN），且该接口只应在内网可达的部署中对外暴露。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.authorization.application.audit_service import QuotaService
from app.core.config import get_settings
from app.core.database.deps import DbSession
from app.models.rbac import QuotaAdjustment
from app.schemas.rbac import QuotaOut
from app.services.recharge_events import publish_quota_credited

router = APIRouter(prefix="/internal", tags=["internal"])


class QuotaCreditRequest(BaseModel):
    model_config = {"extra": "forbid"}

    user_id: uuid.UUID
    tokens: int = Field(gt=0, le=1_000_000_000)
    reference: str = Field(min_length=1, max_length=160)
    source: str = Field(default="recharge", max_length=32)


async def require_internal_token(
    x_internal_token: Annotated[str | None, Header()] = None,
) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="内部接口未启用")
    if x_internal_token != expected:
        raise HTTPException(status_code=401, detail="内部接口鉴权失败")


@router.post("/quota/credit", response_model=QuotaOut, summary="充值到账，按 token 精确加额度（内部）")
async def credit_quota(
    body: QuotaCreditRequest,
    session: DbSession,
    _: None = Depends(require_internal_token),
) -> QuotaOut:
    service = QuotaService(session)

    # 先看这笔 reference 是否已经处理过。重复调用（支付服务重试）不应再次推送事件，
    # 否则前端会弹出重复的「充值到账」提示。
    already_credited = await session.scalar(
        select(QuotaAdjustment.id).where(QuotaAdjustment.reference == body.reference)
    )

    quota = await service.credit_tokens(
        body.user_id,
        body.tokens,
        reference=body.reference,
    )
    payload = QuotaOut.model_validate(quota)

    if already_credited is None:
        # 先提交再推送：浏览器收到事件后可能立刻读库，必须读到已落库的新额度。
        await session.commit()
        summary = await service.get_quota_summary(body.user_id)
        await publish_quota_credited(
            body.user_id,
            reference=body.reference,
            tokens=body.tokens,
            quota=summary,
        )
    return payload
