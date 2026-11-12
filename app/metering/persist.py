"""把分类结果写入调用记录。重复结算键不再插第二行。"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.metering.pricing import price_for, quote_cost
from app.models.metering import (
    LlmUsageBreakdown,
    LlmUsageRecord,
    QuotaTransaction,
    SkillUsageRecord,
    SummaryUpdate,
    ToolUsageRecord,
)
from app.models.mixins import utcnow


def _user(user_id: uuid.UUID | str) -> uuid.UUID:
    return user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))


def _decimal(value: str | None) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(value)


async def persist_meters(
    session: AsyncSession,
    user_id: uuid.UUID | str,
    settlement_key: str,
    meters: list[dict[str, Any]],
    *,
    conversation_id: str | None,
    quota_amount: int,
    request_id: str | None = None,
) -> None:
    """写入模型调用明细，并记一笔 consume 流水。额度数字仍由 QuotaService 扣减。"""
    owner = _user(user_id)
    written: list[LlmUsageRecord] = []
    for index, meter in enumerate(meters):
        key = settlement_key if len(meters) == 1 else f"{settlement_key}:{index}"
        row = await _insert_call(session, owner, key, meter, conversation_id, request_id)
        if row is not None:
            written.append(row)
    anchor = written[0].id if written else None
    await _insert_quota_tx(session, owner, settlement_key, quota_amount, anchor)


async def persist_tool(
    session: AsyncSession,
    user_id: uuid.UUID | str,
    payload: dict[str, Any],
    *,
    conversation_id: str | None,
    task_id: str | None,
) -> None:
    call_id = str(payload.get("tool_call_id") or "")
    name = str(payload.get("tool_name") or "")
    if not call_id or not name:
        return
    owner = _user(user_id)
    existing = await session.scalar(
        select(ToolUsageRecord.id).where(
            ToolUsageRecord.user_id == owner,
            ToolUsageRecord.tool_call_id == call_id,
        )
    )
    if existing is not None:
        return
    try:
        async with session.begin_nested():
            session.add(
                ToolUsageRecord(
                    user_id=owner,
                    conversation_id=conversation_id,
                    task_id=task_id,
                    tool_call_id=call_id,
                    tool_name=name,
                    tool_version=payload.get("tool_version"),
                    status=str(payload.get("status") or "completed"),
                    duration_ms=payload.get("duration_ms"),
                    external_requests=payload.get("external_requests"),
                    external_cost=None,
                    error=payload.get("error"),
                    retry_count=int(payload.get("retry_count") or 0),
                )
            )
            await session.flush()
    except IntegrityError:
        return


async def _insert_call(
    session: AsyncSession,
    owner: uuid.UUID,
    key: str,
    meter: dict[str, Any],
    conversation_id: str | None,
    request_id: str | None,
) -> LlmUsageRecord | None:
    existing = await session.scalar(
        select(LlmUsageRecord).where(
            LlmUsageRecord.user_id == owner,
            LlmUsageRecord.settlement_key == key,
        )
    )
    if existing is not None:
        return existing
    provider = str(meter.get("provider") or "deepseek")
    model = str(meter.get("model") or "")
    pricing = await price_for(session, provider=provider, model=model)
    cost, discount, currency = quote_cost(
        pricing,
        input_tokens=meter.get("input_tokens"),
        output_tokens=meter.get("output_tokens"),
        reasoning_tokens=meter.get("reasoning_tokens"),
        cache_read_tokens=meter.get("cache_read_tokens"),
        cache_write_tokens=meter.get("cache_write_tokens"),
    )
    row = LlmUsageRecord(
        user_id=owner,
        conversation_id=conversation_id,
        request_id=request_id or meter.get("request_id"),
        task_id=meter.get("task_id"),
        settlement_key=key,
        provider=provider,
        model=model,
        runtime_mode=meter.get("runtime_mode"),
        input_tokens=meter.get("input_tokens"),
        output_tokens=meter.get("output_tokens"),
        reasoning_tokens=meter.get("reasoning_tokens"),
        cache_read_tokens=meter.get("cache_read_tokens"),
        cache_write_tokens=meter.get("cache_write_tokens"),
        total_tokens=meter.get("total_tokens"),
        estimated_cost=None,
        actual_cost=cost,
        cache_discount_amount=discount,
        currency=currency,
        usage_source=str(meter.get("usage_source") or "chat"),
        status=str(meter.get("status") or "completed"),
        completed_at=utcnow(),
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        return await session.scalar(
            select(LlmUsageRecord).where(
                LlmUsageRecord.user_id == owner,
                LlmUsageRecord.settlement_key == key,
            )
        )
    for item in meter.get("breakdown") or []:
        if not isinstance(item, dict):
            continue
        session.add(
            LlmUsageBreakdown(
                usage_record_id=row.id,
                direction=str(item.get("direction") or "input"),
                category=str(item.get("category") or "other_input"),
                token_count=item.get("token_count"),
                attribution_method=str(item.get("attribution_method") or "unavailable"),
                confidence=_decimal(item.get("confidence")),
                metadata_json=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
            )
        )
    for skill in meter.get("skills") or []:
        if not isinstance(skill, dict):
            continue
        skill_id = str(skill.get("skill_id") or skill.get("skill_name") or "")
        if not skill_id:
            continue
        execution_id = f"{key}:{skill_id}"
        found = await session.scalar(
            select(SkillUsageRecord.id).where(
                SkillUsageRecord.user_id == owner,
                SkillUsageRecord.skill_execution_id == execution_id,
            )
        )
        if found is not None:
            continue
        session.add(
            SkillUsageRecord(
                user_id=owner,
                llm_usage_id=row.id,
                skill_id=skill_id,
                skill_name=skill.get("skill_name"),
                skill_version=skill.get("skill_version"),
                skill_execution_id=execution_id,
                conversation_id=conversation_id,
                instruction_tokens=skill.get("instruction_tokens"),
                direct_cost=None,
                status=str(meter.get("status") or "completed"),
            )
        )
    if str(meter.get("usage_source") or "") == "summary":
        await _insert_summary(session, owner, key, meter, conversation_id, request_id or meter.get("request_id"))
    await session.flush()
    return row


def _kept_breakdown(meter: dict[str, Any], direction: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in meter.get("breakdown") or []:
        if not isinstance(item, dict) or item.get("direction") != direction:
            continue
        count = item.get("token_count")
        method = str(item.get("attribution_method") or "unavailable")
        if count is None or int(count) <= 0 or method == "unavailable":
            continue
        rows.append(
            {
                "direction": direction,
                "category": str(item.get("category") or ""),
                "token_count": int(count),
                "attribution_method": method,
            }
        )
    return rows


async def _insert_summary(
    session: AsyncSession,
    owner: uuid.UUID,
    key: str,
    meter: dict[str, Any],
    conversation_id: str | None,
    request_id: str | None,
) -> None:
    existing = await session.scalar(
        select(SummaryUpdate.id).where(
            SummaryUpdate.user_id == owner,
            SummaryUpdate.settlement_key == key,
        )
    )
    if existing is not None:
        return
    text = str(meter.get("summary_text") or "").strip()
    try:
        async with session.begin_nested():
            session.add(
                SummaryUpdate(
                    user_id=owner,
                    conversation_id=conversation_id,
                    settlement_key=key,
                    request_id=str(request_id)[:64] if request_id else None,
                    provider=str(meter.get("provider") or "deepseek"),
                    model=str(meter.get("model") or ""),
                    summary_text=text[:2000],
                    called_at=utcnow(),
                    input_tokens=meter.get("input_tokens"),
                    output_tokens=meter.get("output_tokens"),
                    total_tokens=meter.get("total_tokens"),
                    cache_read_tokens=meter.get("cache_read_tokens"),
                    cache_write_tokens=meter.get("cache_write_tokens"),
                    input_breakdown=_kept_breakdown(meter, "input"),
                    output_breakdown=_kept_breakdown(meter, "output"),
                    cache_breakdown=_kept_breakdown(meter, "cache"),
                    tool_count=int(meter.get("tool_count") or 0),
                    call_count=1,
                )
            )
            await session.flush()
    except IntegrityError:
        return


async def _insert_quota_tx(
    session: AsyncSession,
    owner: uuid.UUID,
    settlement_key: str,
    amount: int,
    llm_usage_id: uuid.UUID | None,
) -> None:
    existing = await session.scalar(
        select(QuotaTransaction.id).where(
            QuotaTransaction.user_id == owner,
            QuotaTransaction.settlement_key == settlement_key,
            QuotaTransaction.action == "consume",
        )
    )
    if existing is not None:
        return
    try:
        async with session.begin_nested():
            session.add(
                QuotaTransaction(
                    user_id=owner,
                    llm_usage_id=llm_usage_id,
                    settlement_key=settlement_key,
                    action="consume",
                    amount=amount,
                )
            )
            await session.flush()
    except IntegrityError:
        return


def usage_public(usage: Any) -> dict[str, Any]:
    """SSE 里的 usage。未知分类不补 0。"""
    payload: dict[str, Any] = {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        "billing": "provider",
    }
    reasoning = getattr(usage, "reasoning_tokens", None)
    cache_read = getattr(usage, "cache_read_tokens", None)
    cache_write = getattr(usage, "cache_write_tokens", None)
    if reasoning is not None:
        payload["reasoning_tokens"] = int(reasoning)
    if cache_read is not None:
        payload["cache_read_tokens"] = int(cache_read)
    if cache_write is not None:
        payload["cache_write_tokens"] = int(cache_write)
    return payload
