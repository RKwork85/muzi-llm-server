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
            )
            await session.commit()
    except Exception:
        logger.exception("tool usage record failed user_id=%s", user_id)


def quota_event_observer(inner: Callable[..., None] | None, user_id: str | None, task_key: str):
    """在 Agent 每个模型步完成后结算。调用方在该步结束前 await drain。"""

    pending: list[asyncio.Task[None]] = []

    def observe(event) -> None:
        if inner is not None:
            inner(event)
        if user_id is None or getattr(event.type, "value", event.type) != "agent.model.completed":
            return
        data = event.data if isinstance(getattr(event, "data", None), dict) else {}
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
                prompt_tokens=prompt,
                completion_tokens=completion,
                extra={"task_id": task_key, "step": step},
            )

        pending.append(asyncio.create_task(_settle()))

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
