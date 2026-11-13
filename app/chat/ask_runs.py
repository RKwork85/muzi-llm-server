"""Ask 运行的打开与终态更新。不调用模型，也不结算额度。"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ask_run import AskRun

TERMINAL_ASK_STATUSES = frozenset({"completed", "failed", "cancelled"})


class AskRunDecision(str, Enum):
    CREATED = "created"
    REPLAY = "replay"
    CONFLICT = "conflict"
    IN_PROGRESS = "in_progress"


@dataclass(frozen=True)
class AskRunOpen:
    decision: AskRunDecision
    run_id: uuid.UUID | None
    external_id: str
    status: str | None = None


def request_fingerprint(user_text: str) -> str:
    """同一请求的内容摘要。不保存提示词原文。"""

    return hashlib.sha256(user_text.encode("utf-8")).hexdigest()


def can_replace_status(current: str, proposed: str) -> bool:
    """终态只从 running 进入一次。迟到的 running 不能覆盖终态。"""

    if current in TERMINAL_ASK_STATUSES:
        return False
    return current == "running" and proposed in TERMINAL_ASK_STATUSES


async def open_ask_run(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    conversation_id: uuid.UUID,
    external_id: str,
    external_id_type: str,
    fingerprint: str,
) -> AskRunOpen:
    run_id = uuid.uuid4()
    try:
        async with session.begin_nested():
            session.add(
                AskRun(
                    id=run_id,
                    owner_id=owner_id,
                    conversation_id=conversation_id,
                    surface="ask",
                    external_id=external_id,
                    external_id_type=external_id_type,
                    status="running",
                    status_reason="opened",
                    request_fingerprint=fingerprint,
                    started_at=datetime.now(timezone.utc),
                )
            )
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(
            select(AskRun).where(
                AskRun.owner_id == owner_id,
                AskRun.conversation_id == conversation_id,
                AskRun.external_id == external_id,
            )
        )
        if existing is None:
            raise
        if existing.request_fingerprint != fingerprint:
            return AskRunOpen(AskRunDecision.CONFLICT, None, external_id)
        if existing.status in TERMINAL_ASK_STATUSES:
            return AskRunOpen(AskRunDecision.REPLAY, existing.id, external_id, existing.status)
        return AskRunOpen(AskRunDecision.IN_PROGRESS, None, external_id, existing.status)
    return AskRunOpen(AskRunDecision.CREATED, run_id, external_id, "running")


async def seal_ask_run(
    session: AsyncSession,
    run_id: uuid.UUID,
    owner_id: uuid.UUID,
    status: str,
    reason: str,
) -> bool:
    if status not in TERMINAL_ASK_STATUSES:
        return False
    result = await session.execute(
        update(AskRun)
        .where(
            AskRun.id == run_id,
            AskRun.owner_id == owner_id,
            AskRun.status == "running",
        )
        .values(
            status=status,
            status_reason=reason,
            finished_at=datetime.now(timezone.utc),
        )
    )
    return int(result.rowcount or 0) == 1
