"""按当前用户读取一轮 Ask 的已落库事件和用量。只读，不结算。"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.ask_runs import TERMINAL_ASK_STATUSES
from app.chat.run_registry import chat_run_registry
from app.models.ask_run import AskRun
from app.models.metering import LlmUsageRecord, ToolUsageRecord
from app.models.planning import PersistentPlanningEvent
from app.models.rbac import UsageRecord
from app.models.tables import ConversationSession
from app.schemas.chat_run import (
    ChatRunCall,
    ChatRunDetailResponse,
    ChatRunToolUsage,
    ChatRunUsage,
)

_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_CHAT_EVENTS = (
    "chat.run.started",
    "chat.done",
    "chat.error",
    "chat.cancelled",
)
_TERMINAL = {
    "chat.done": "completed",
    "chat.error": "failed",
    "chat.cancelled": "cancelled",
}


def chat_run_event_statement(user_id: uuid.UUID, run_id: str):
    return (
        select(PersistentPlanningEvent)
        .join(ConversationSession, ConversationSession.id == PersistentPlanningEvent.conversation_id)
        .where(
            ConversationSession.user_id == user_id,
            PersistentPlanningEvent.event_type.in_(_CHAT_EVENTS),
            PersistentPlanningEvent.payload["run_id"].as_string() == run_id,
        )
        .order_by(PersistentPlanningEvent.conversation_id, PersistentPlanningEvent.sequence)
    )


def _same_value(values: list[str | None]) -> str | None:
    present = {item for item in values if item}
    if len(present) != 1:
        return None
    return present.pop()


def _sum_complete(values: list[int | None]) -> int | None:
    if not values or any(item is None for item in values):
        return None
    return sum(int(item) for item in values if item is not None)


def _dedupe(rows: list[Any], key_name: str) -> list[Any]:
    seen: set[str] = set()
    kept: list[Any] = []
    for row in rows:
        key = str(getattr(row, key_name, "") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(row)
    return kept


def _owned(rows: list[Any], user_id: uuid.UUID) -> list[Any]:
    return [row for row in rows if getattr(row, "user_id", user_id) == user_id]


def resolve_chat_run_status(events: list[Any], *, active: bool) -> tuple[str, str, str, Any | None]:
    """终态事件优先。没有终态时，只有本进程注册表里的活动任务才是 running。"""
    terminals = [event for event in events if event.event_type in _TERMINAL]
    if terminals:
        latest = max(terminals, key=lambda event: (event.sequence, event.occurred_at))
        reason = "latest_terminal_event" if len(terminals) > 1 else "terminal_event"
        return _TERMINAL[latest.event_type], reason, "plan_events", latest
    if active:
        return "running", "registry_active", "run_registry", None
    return "unknown", "terminal_event_missing", "historical_inference", None


def _pick_events(events: list[Any]) -> list[Any]:
    grouped: dict[Any, list[Any]] = {}
    for event in events:
        grouped.setdefault(event.conversation_id, []).append(event)

    def rank(conversation_id: Any) -> datetime:
        starts = [item.occurred_at for item in grouped[conversation_id] if item.event_type == "chat.run.started"]
        if not starts:
            return min(item.occurred_at for item in grouped[conversation_id])
        return max(starts)

    chosen = max(grouped, key=rank)
    return sorted(grouped[chosen], key=lambda item: item.sequence)


def assemble_chat_run(
    run_id: str,
    user_id: uuid.UUID,
    events: list[Any],
    llm_rows: list[Any],
    usage_rows: list[Any],
    tool_rows: list[Any],
    *,
    active: bool = False,
) -> ChatRunDetailResponse | None:
    owned_events = [event for event in events if str(event.payload.get("run_id") or "") == run_id]
    if not owned_events:
        return None
    selected = _pick_events(owned_events)
    conversation_id = str(selected[0].conversation_id)
    status, status_reason, status_source, terminal = resolve_chat_run_status(selected, active=active)
    started = next((event for event in selected if event.event_type == "chat.run.started"), None)
    started_at = started.occurred_at if started is not None else None
    finished_at = terminal.occurred_at if terminal is not None else None
    duration_ms = None
    if started_at is not None and finished_at is not None:
        duration_ms = max(0, int((finished_at - started_at).total_seconds() * 1000))

    llm = [
        row
        for row in _dedupe(_owned(llm_rows, user_id), "settlement_key")
        if str(getattr(row, "conversation_id", "") or "") == conversation_id
        and str(row.settlement_key).startswith(f"{run_id}:")
    ]
    usage = [
        row
        for row in _dedupe(_owned(usage_rows, user_id), "settlement_key")
        if str((row.context_json or {}).get("conversation_id") or "") == conversation_id
        and str(row.settlement_key or "").startswith(f"{run_id}:")
    ]
    tools = [
        row
        for row in _owned(tool_rows, user_id)
        if str(getattr(row, "conversation_id", "") or "") == conversation_id
        and str(getattr(row, "task_id", "") or "") == run_id
    ]
    llm.sort(key=lambda row: (getattr(row, "created_at", None) or datetime.min, row.settlement_key))

    if llm:
        usage_body = ChatRunUsage(
            input_tokens=_sum_complete([row.input_tokens for row in llm]),
            output_tokens=_sum_complete([row.output_tokens for row in llm]),
            total_tokens=_sum_complete([row.total_tokens for row in llm]),
            reasoning_tokens=_sum_complete([row.reasoning_tokens for row in llm]),
            calls=[
                ChatRunCall(
                    settlement_key=row.settlement_key,
                    model=row.model or None,
                    status=row.status,
                    input_tokens=row.input_tokens,
                    output_tokens=row.output_tokens,
                    reasoning_tokens=row.reasoning_tokens,
                    total_tokens=row.total_tokens,
                )
                for row in llm
            ],
        )
        mode = _same_value([getattr(row, "runtime_mode", None) for row in llm])
        model = _same_value([row.model for row in llm])
    else:
        usage_body = ChatRunUsage(
            input_tokens=_sum_complete([(row.context_json or {}).get("prompt_tokens") for row in usage]) if usage else None,
            output_tokens=_sum_complete([(row.context_json or {}).get("completion_tokens") for row in usage]) if usage else None,
            total_tokens=_sum_complete([row.amount for row in usage]) if usage else None,
            reasoning_tokens=None,
        )
        mode = None
        model = _same_value([(row.context_json or {}).get("model") for row in usage]) if usage else None

    if usage:
        settled = [row.amount for row in usage if row.result == "settled"]
        usage_body.quota_consumed = sum(int(amount) for amount in settled) if settled else 0
    if tools:
        usage_body.tool_usage = [
            ChatRunToolUsage(
                tool_name=row.tool_name,
                status=row.status,
                duration_ms=row.duration_ms,
                external_cost=None if row.external_cost is None else str(row.external_cost),
            )
            for row in tools
        ]
    return ChatRunDetailResponse(
        run_id=run_id,
        conversation_id=conversation_id,
        mode=mode,
        model=model,
        status=status,
        status_reason=status_reason,
        status_source=status_source,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        usage=usage_body,
    )


def status_from_ask_run(status: str, *, active: bool) -> tuple[str, str, str]:
    """数据库里的 running 只表示尚未写入终态，不表示任务仍在本进程执行。"""

    if status in TERMINAL_ASK_STATUSES:
        return status, "terminal_row", "ask_runs"
    if active:
        return "running", "registry_active", "run_registry"
    return "unknown", "terminal_event_missing", "historical_inference"


async def _detail_for_ask_run(
    session: AsyncSession,
    user_id: uuid.UUID,
    row: AskRun,
) -> ChatRunDetailResponse:
    run_id = str(row.id)
    events = list(await session.scalars(chat_run_event_statement(user_id, run_id)))
    owned = [event for event in events if event.conversation_id == row.conversation_id]
    prefix = f"{run_id}:"
    llm_rows = list(
        await session.scalars(
            select(LlmUsageRecord).where(
                LlmUsageRecord.user_id == user_id,
                LlmUsageRecord.conversation_id == str(row.conversation_id),
                LlmUsageRecord.settlement_key.startswith(prefix),
            )
        )
    )
    usage_rows = list(
        await session.scalars(
            select(UsageRecord).where(
                UsageRecord.user_id == user_id,
                UsageRecord.source == "chat",
                UsageRecord.settlement_key.startswith(prefix),
                UsageRecord.context_json["conversation_id"].as_string() == str(row.conversation_id),
            )
        )
    )
    tool_rows = list(
        await session.scalars(
            select(ToolUsageRecord).where(
                ToolUsageRecord.user_id == user_id,
                ToolUsageRecord.conversation_id == str(row.conversation_id),
                ToolUsageRecord.task_id == run_id,
            )
        )
    )
    active = await chat_run_registry.is_active(run_id, str(user_id))
    detail = assemble_chat_run(run_id, user_id, owned, llm_rows, usage_rows, tool_rows, active=active)
    status, reason, source = status_from_ask_run(row.status, active=active)
    if detail is None:
        return ChatRunDetailResponse(
            run_id=run_id,
            conversation_id=str(row.conversation_id),
            mode="ask",
            status=status,  # type: ignore[arg-type]
            status_reason=reason,
            status_source=source,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )
    return detail.model_copy(
        update={
            "status": status,
            "status_reason": reason,
            "status_source": source,
            "started_at": row.started_at,
            "finished_at": row.finished_at if row.status in TERMINAL_ASK_STATUSES else detail.finished_at,
        }
    )


async def load_chat_run_detail(
    session: AsyncSession,
    user_id: uuid.UUID,
    run_id: str,
) -> ChatRunDetailResponse | None:
    if _RUN_ID.fullmatch(run_id) is None:
        return None
    try:
        parsed = uuid.UUID(run_id)
    except ValueError:
        parsed = None
    if parsed is not None:
        row = await session.scalar(select(AskRun).where(AskRun.id == parsed, AskRun.owner_id == user_id))
        if row is not None:
            return await _detail_for_ask_run(session, user_id, row)
    events = list(await session.scalars(chat_run_event_statement(user_id, run_id)))
    if not events:
        return None
    conversations = {event.conversation_id for event in events}
    if len(conversations) != 1:
        return None
    conversation_id = str(next(iter(conversations)))
    prefix = f"{run_id}:"
    llm_rows = list(
        await session.scalars(
            select(LlmUsageRecord).where(
                LlmUsageRecord.user_id == user_id,
                LlmUsageRecord.conversation_id == conversation_id,
                LlmUsageRecord.settlement_key.startswith(prefix),
            )
        )
    )
    usage_rows = list(
        await session.scalars(
            select(UsageRecord).where(
                UsageRecord.user_id == user_id,
                UsageRecord.source == "chat",
                UsageRecord.settlement_key.startswith(prefix),
                UsageRecord.context_json["conversation_id"].as_string() == conversation_id,
            )
        )
    )
    tool_rows = list(
        await session.scalars(
            select(ToolUsageRecord).where(
                ToolUsageRecord.user_id == user_id,
                ToolUsageRecord.conversation_id == conversation_id,
                ToolUsageRecord.task_id == run_id,
            )
        )
    )
    active = await chat_run_registry.is_active(run_id, str(user_id))
    return assemble_chat_run(run_id, user_id, events, llm_rows, usage_rows, tool_rows, active=active)
