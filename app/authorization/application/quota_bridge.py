"""在独立事务里结算额度，避免拖垮对话或 Agent 自己的事务。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Callable

from app.authorization.application.audit_service import QuotaService, Settlement

logger = logging.getLogger("muzi_llm")


async def settle_in_new_session(
    user_id: uuid.UUID | str,
    *,
    source: str,
    settlement_key: str,
    amount: int,
    kind: str,
    model: str | None = None,
    conversation_id: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    image_count: int | None = None,
    extra: dict[str, Any] | None = None,
    meters: list[dict[str, Any]] | None = None,
    trace_id: str | None = None,
) -> Settlement | None:
    from app.core.database.session import get_session_factory

    try:
        factory = get_session_factory()
    except RuntimeError:
        return None
    try:
        async with factory() as session:
            result = await QuotaService(session).settle(
                user_id,
                source=source,
                settlement_key=settlement_key,
                amount=amount,
                kind=kind,
                model=model,
                conversation_id=conversation_id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                image_count=image_count,
                extra=extra,
                trace_id=trace_id,
            )
            if meters:
                from app.metering.persist import persist_meters

                await persist_meters(
                    session,
                    user_id,
                    settlement_key,
                    meters,
                    conversation_id=conversation_id,
                    quota_amount=max(0, amount),
                    request_id=settlement_key.split(":", 1)[0],
                    trace_id=trace_id,
                )
            await session.commit()
            return result
    except Exception:
        logger.exception("quota settle failed user_id=%s source=%s", user_id, source)
        return None


async def record_tool_in_new_session(
    user_id: uuid.UUID | str,
    payload: dict[str, Any],
    *,
    conversation_id: str | None,
    task_id: str | None,
    trace_id: str | None = None,
) -> None:
    from app.core.database.session import get_session_factory
    from app.metering.persist import persist_tool

    try:
        factory = get_session_factory()
    except RuntimeError:
        return
    try:
        async with factory() as session:
            await persist_tool(
                session,
                user_id,
                payload,
                conversation_id=conversation_id,
                task_id=task_id,
                trace_id=trace_id,
            )
            await session.commit()
    except Exception:
        logger.exception("tool usage record failed user_id=%s", user_id)


async def record_skill_in_new_session(
    user_id: uuid.UUID | str,
    payload: dict[str, Any],
    *,
    conversation_id: str | None = None,
    task_id: str | None = None,
    trace_id: str | None = None,
) -> None:
    from app.core.database.session import get_session_factory
    from app.metering.persist import persist_skill

    try:
        factory = get_session_factory()
    except RuntimeError:
        return
    try:
        async with factory() as session:
            await persist_skill(
                session,
                user_id,
                payload,
                conversation_id=conversation_id,
                task_id=task_id,
                trace_id=trace_id,
            )
            await session.commit()
    except Exception:
        logger.exception("skill usage record failed user_id=%s", user_id)


def quota_event_observer(inner: Callable[..., None] | None, user_id: str | None, task_key: str):
    """在 Agent 每个模型步完成后结算，并在工具执行后记 ToolUsageRecord。

    调用方在该步结束前 await drain。
    """

    pending: list[asyncio.Task[None]] = []

    def observe(event) -> None:
        if inner is not None:
            inner(event)
        if user_id is None:
            return
        event_type = getattr(event.type, "value", event.type)
        data = event.data if isinstance(getattr(event, "data", None), dict) else {}
        if event_type == "agent.model.completed":
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            step = data.get("step")
            amount = int(data.get("total_tokens") or usage.get("total_tokens") or 0)
            prompt = int(data.get("prompt_tokens") or usage.get("prompt_tokens") or 0)
            completion = int(data.get("completion_tokens") or usage.get("completion_tokens") or 0)

            async def _settle() -> None:
                await settle_in_new_session(
                    user_id,
                    source="agent",
                    settlement_key=f"{task_key}:{step}",
                    amount=amount,
                    kind="chat",
                    model=str(data.get("model") or ""),
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                    extra={"task_id": task_key, "step": step},
                    meters=[_agent_meter(data, prompt, completion, task_key, step)],
                    trace_id=task_key,
                )

            pending.append(asyncio.create_task(_settle()))
            return
        if event_type in {"agent.tool.completed", "agent.tool.failed"}:
            _observe_tool(user_id, task_key, data, pending)

    async def drain() -> None:
        tasks = list(pending)
        pending.clear()
        if not tasks:
            return
        done = await asyncio.gather(*tasks, return_exceptions=True)
        for item in done:
            if isinstance(item, Exception):
                logger.exception("agent quota settle failed task=%s", task_key)

    observe.drain = drain  # type: ignore[attr-defined]
    return observe


def _observe_tool(
    user_id: str,
    task_key: str,
    data: dict[str, Any],
    pending: list[asyncio.Task[None]],
) -> None:
    """工具执行后：写 ToolUsageRecord；外部工具按换算的 token 扣额度。"""
    tool_call_id = str(data.get("tool_call_id") or "")
    tool_name = str(data.get("tool_name") or "")
    if not tool_call_id or not tool_name:
        return
    status = str(data.get("status") or "completed")
    payload: dict[str, Any] = {
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "status": status,
        "duration_ms": data.get("duration_ms"),
        "error": data.get("error_code") or None,
        "retry_count": 0,
    }
    external_requests = data.get("external_requests")
    if external_requests is not None:
        payload["external_requests"] = int(external_requests or 0)
    external_cost = data.get("external_cost")
    if external_cost is not None:
        payload["external_cost"] = external_cost
    currency = data.get("currency")
    if currency is not None:
        payload["currency"] = currency

    async def _record() -> None:
        await record_tool_in_new_session(
            user_id,
            payload,
            conversation_id=None,
            task_id=task_key,
            trace_id=task_key,
        )
        external_tokens = int(data.get("external_tokens") or 0)
        if external_tokens > 0:
            await settle_in_new_session(
                user_id,
                source="tool",
                settlement_key=f"{task_key}:tool:{tool_call_id}",
                amount=external_tokens,
                kind="chat",
                extra={"task_id": task_key, "tool_name": tool_name, "external": True},
                trace_id=task_key,
            )

    pending.append(asyncio.create_task(_record()))


def _agent_meter(
    data: dict[str, Any],
    prompt: int,
    completion: int,
    task_key: str,
    step: object,
) -> dict[str, Any]:
    """把一个 agent 模型步转成 meter，走 persist_meters 写 breakdown。

    事件载荷没有 messages 全文，只能按 Provider 总量拆 other_input/other_output；
    详细的 system/user/history/tool_result 分类留到 M3.1/M3.2 再把 messages 接入。
    """
    from app.core.config import get_settings
    from app.metering.attribute import build_meter
    from app.metering.trace import new_span_id
    from app.providers.base import UsageStats

    usage = UsageStats(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        reasoning_tokens=_opt_int(data.get("reasoning_tokens")),
        cache_read_tokens=_opt_int(data.get("cache_read_tokens")),
        cache_write_tokens=_opt_int(data.get("cache_write_tokens")),
    )
    meter = build_meter(
        provider_name=get_settings().llm_provider,
        model=str(data.get("model") or ""),
        runtime_mode="agent",
        usage_source="chat",
        status="completed",
        usage=usage,
        messages=[],
        tools=None,
        system_text="",
        developer_text="",
        skill_text="",
        skill_refs=None,
        output_text=str(data.get("output_text") or ""),
        tool_calls=[],
        structured=False,
    )
    meter["trace_id"] = task_key
    meter["task_id"] = task_key
    meter["span_id"] = new_span_id()
    meter["parent_span_id"] = None
    return meter


def _opt_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None
