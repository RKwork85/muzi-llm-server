"""充值下单代理接口。

前端带登录态调用本接口；本服务从登录态取真实 user_id，转发给独立支付服务下单。
支付服务只信任内部密钥，且只在收到该密钥时才接受 user_id，因此用户无法伪造身份。

与支付服务的交互仅此一处：充值下单。收款配置等均在支付服务自己管理，不经过主服务。
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_authenticated_user
from app.authorization.dependencies import require_permission
from app.authorization.application.audit_service import QuotaService
from app.core.config import get_settings
from app.core.database.deps import DbSession
from app.core.exceptions import (
    FeatureNotReadyError,
    InvalidRequestError,
    RechargeOrderNotFoundError,
    UpstreamError,
)
from app.models.tables import User

router = APIRouter(prefix="/recharge", tags=["recharge"])


class RechargeCreateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    tokens: int = Field(gt=0, le=1_000_000_000)


class RechargeCreateResponse(BaseModel):
    order_id: str
    amount_yuan: str
    tokens: int
    qrcode_url: str = ""
    # 内联二维码（data:image/png;base64,...），由支付服务生成后原样透传。
    # 前端优先用它：`qrcode_url` 依赖支付服务的对外地址，跨设备/外网时容易裂图。
    qrcode_data_url: str = ""
    # 单独的微信 / 支付宝收款码，前端按所选支付方式只展示对应的一张。
    wechat_qrcode_data_url: str = ""
    alipay_qrcode_data_url: str = ""


class RechargeOrderStatus(BaseModel):
    order_id: str
    status: str
    tokens: int
    amount_yuan: str
    created_at: str | None = None
    paid_at: str | None = None


@router.post("/create", response_model=RechargeCreateResponse, summary="创建充值订单（登录态）")
async def create_recharge(
    body: RechargeCreateRequest,
    user: Annotated[User, Depends(require_permission("recharge.write"))],
    session: DbSession,
) -> RechargeCreateResponse:
    settings = get_settings()
    if not settings.internal_api_token:
        raise FeatureNotReadyError("充值服务未启用")

    # 不限量账号充值不会增加任何额度（额度上限永远是「不限量」），
    # 必须在付款前拦住，避免用户付了钱却看不到任何变化。
    # 额度行可能被管理端「删除用户额度信息」清掉，所以这里不能只看现有额度行，
    # 还要按角色识别不限量账号。
    if await QuotaService(session).is_unlimited(user.id):
        raise InvalidRequestError("当前账号为不限量额度，无需充值")

    url = f"{settings.pay_service_base_url.rstrip('/')}/recharge/create"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                headers={"X-Internal-Token": settings.internal_api_token},
                json={"user_id": str(user.id), "tokens": body.tokens},
            )
    except httpx.HTTPError as exc:
        raise UpstreamError("充值服务暂时不可用") from exc

    if resp.status_code == 429:
        raise UpstreamError("待收款订单过多，请稍后再试")
    if resp.status_code == 409:
        raise UpstreamError("订单创建冲突，请重试")
    if resp.status_code != 200:
        raise UpstreamError("充值服务返回异常")

    data = resp.json()
    return RechargeCreateResponse(**data)


@router.get(
    "/orders/{order_id}",
    response_model=RechargeOrderStatus,
    summary="查询充值订单状态（登录态）",
)
async def get_recharge_order(
    order_id: str,
    user: Annotated[User, Depends(get_authenticated_user)],
) -> RechargeOrderStatus:
    """查询自己某笔订单是否已到账。

    用户的「查询到账」按钮与 SSE 掉线时的兜底轮询都用它。
    user_id 从登录态注入，支付服务据此校验归属，查不到他人订单。
    """
    settings = get_settings()
    if not settings.internal_api_token:
        raise FeatureNotReadyError("充值服务未启用")

    url = f"{settings.pay_service_base_url.rstrip('/')}/recharge/order/{order_id}"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                url,
                params={"user_id": str(user.id)},
                headers={"X-Internal-Token": settings.internal_api_token},
            )
    except httpx.HTTPError as exc:
        raise UpstreamError("充值服务暂时不可用") from exc

    if resp.status_code == 404:
        raise RechargeOrderNotFoundError()
    if resp.status_code != 200:
        raise UpstreamError("充值服务返回异常")

    return RechargeOrderStatus(**resp.json())
