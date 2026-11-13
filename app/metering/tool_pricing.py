"""工具成本换算：外部货币成本 → token 额度。

工具本身不直接扣额度，工具结果以 tool_result 形式计入下一轮模型输入。
但外部工具（web_search / web_fetch）有真实货币成本，需要按「成功请求次数」换算成
token 额度扣减，同时把货币成本单独记账用于财务对账。

换算公式：
    tokens = price / unit * requests_per_call / TOKEN_PRICE
    其中 TOKEN_PRICE 是「1 token 的人民币成本」，由配置 TOKEN_PRICE_CNY 注入。

计费口径是「成功请求」：失败的外部调用不计费。
"""

from __future__ import annotations

from decimal import Decimal, ROUND_UP
from typing import Any

_TOKEN_PRICE_DEFAULT = Decimal("0.000001")  # 1 token = 0.000001 元（占位，需按实际定价调整）


def get_token_price(currency: str = "CNY") -> Decimal:
    """返回「1 token 的人民币成本」。非 CNY 币种暂按同一汇率换算。

    汇率从配置读取，不硬编码，因为厂商定价会变。
    """
    price: Decimal
    try:
        from app.core.config import get_settings

        raw = get_settings().token_price_cny
        price = Decimal(str(raw))
    except Exception:
        price = _TOKEN_PRICE_DEFAULT
    if price <= 0:
        return _TOKEN_PRICE_DEFAULT
    return price


def quote_tool_money(pricing: Any | None, *, success: bool = True) -> Decimal | None:
    """返回本次工具调用的真实货币成本（财务对账用，不参与额度）。

    非 external 模型或失败调用返回 None。
    """
    if pricing is None or getattr(pricing, "model", "free") != "external" or not success:
        return None
    price = Decimal(str(getattr(pricing, "external_price", "0") or "0"))
    unit = max(1, int(getattr(pricing, "external_unit", 1) or 1))
    per_call = Decimal(str(getattr(pricing, "requests_per_call", 1) or 1))
    return price / unit * per_call


def quote_tool_tokens(pricing: Any | None, *, success: bool = True) -> int:
    """把一次工具调用换算成 token 额度。

    - free：0（结果走 tool_result 计入输入，不另扣）。
    - fixed_tokens：每次调用固定扣固定值，仅成功时计费（失败返回 0）。
    - external：外部价格换算成 token（成功请求才计费，向上取整）。
    """
    if pricing is None:
        return 0
    model = getattr(pricing, "model", "free")
    if model == "free":
        return 0
    if model == "fixed_tokens":
        if not success:
            return 0
        return max(0, int(getattr(pricing, "fixed_tokens", 0) or 0))
    if model == "external":
        money = quote_tool_money(pricing, success=success)
        if money is None or money <= 0:
            return 0
        token_price = get_token_price(str(getattr(pricing, "currency", "CNY") or "CNY"))
        if token_price <= 0:
            return 0
        return int((money / token_price).to_integral_value(rounding=ROUND_UP))
    return 0
