"""按价格版本计算费用。没有生效价格时返回空，不用统一单价冒充账单。"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.metering import ModelPricingVersion


def _units(tokens: int | None, price: Decimal | None, unit: int) -> Decimal:
    if tokens is None or price is None or unit <= 0:
        return Decimal("0")
    return (Decimal(tokens) * price) / Decimal(unit)


async def price_for(
    session: AsyncSession,
    *,
    provider: str,
    model: str,
    at: datetime | None = None,
) -> ModelPricingVersion | None:
    moment = at or datetime.now(timezone.utc)
    stmt = (
        select(ModelPricingVersion)
        .where(
            ModelPricingVersion.provider == provider,
            ModelPricingVersion.model == model,
            ModelPricingVersion.effective_from <= moment,
            or_(ModelPricingVersion.effective_to.is_(None), ModelPricingVersion.effective_to > moment),
        )
        .order_by(ModelPricingVersion.effective_from.desc())
        .limit(1)
    )
    return await session.scalar(stmt)


def quote_cost(
    pricing: ModelPricingVersion | None,
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    reasoning_tokens: int | None,
    cache_read_tokens: int | None,
    cache_write_tokens: int | None,
) -> tuple[Decimal | None, Decimal | None, str | None]:
    """返回 (actual_cost, cache_discount, currency)。无价格版本时都是 None。"""
    if pricing is None or (input_tokens is None and output_tokens is None):
        return None, None, None
    unit = pricing.price_unit
    cache_read = cache_read_tokens or 0
    uncached = None if input_tokens is None else max(0, input_tokens - cache_read)
    read_price = pricing.cache_read_price if pricing.cache_read_price is not None else pricing.input_price
    output_body = None
    if output_tokens is not None and reasoning_tokens is not None:
        output_body = max(0, output_tokens - reasoning_tokens)
    elif output_tokens is not None and reasoning_tokens is None:
        output_body = output_tokens
    reasoning_price = pricing.reasoning_price if pricing.reasoning_price is not None else pricing.output_price
    cost = (
        _units(uncached, pricing.input_price, unit)
        + _units(cache_read_tokens, read_price, unit)
        + _units(cache_write_tokens, pricing.cache_write_price, unit)
        + _units(output_body, pricing.output_price, unit)
        + _units(reasoning_tokens, reasoning_price, unit)
    )
    discount = None
    if cache_read_tokens and pricing.cache_read_price is not None and pricing.input_price is not None:
        discount = _units(cache_read_tokens, pricing.input_price - pricing.cache_read_price, unit)
    return cost, discount, pricing.currency
