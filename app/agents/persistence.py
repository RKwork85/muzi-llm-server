"""Durable audit writer for Agent runs that originate from planning handoff."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.models import AgentEventType, EventEnvelope
from app.models.tables import AgentTask, ExecutionEvent, TaskExecution, User
from app.planning.contracts import PlanStatus, PlanningEventType
from app.planning.sql_store import SqlPlanningStore

logger = logging.getLogger("muzi_llm")


class AgentRunPersistence:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        task_id: uuid.UUID,
        execution_id: uuid.UUID,
        result_view: Callable[[], dict[str, object]],
    ) -> None:
        self._factory = factory
        self._task_id = task_id
        self._execution_id = execution_id
        self._result_view = result_view
        self._pending: set[asyncio.Task[None]] = set()
        self._write_lock = asyncio.Lock()

    def __call__(self, event: EventEnvelope) -> None:
        task = asyncio.create_task(self._write(event))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def drain(self) -> None:
        while self._pending:
            await asyncio.gather(*tuple(self._pending), return_exceptions=True)

    async def _write(self, event: EventEnvelope) -> None:
        async with self._write_lock:
            await self._write_serial(event)

    async def _write_serial(self, event: EventEnvelope) -> None:
        try:
            async with self._factory() as session:
                task = await session.get(AgentTask, self._task_id)
                execution = await session.get(TaskExecution, self._execution_id)
                if task is None or execution is None:
                    return
                session.add(
                    ExecutionEvent(
                        task_id=self._task_id,
                        execution_id=self._execution_id,
                        event_type=event.type.value,
                        payload=event.model_dump(mode="json"),
                        sequence=event.sequence,
                    )
                )
                now = datetime.now(UTC)
                if event.type == AgentEventType.STARTED:
                    task.status = "running"
                    execution.status = "running"
                    execution.started_at = now
                elif event.type == AgentEventType.STEP_STARTED:
                    # 执行期自动进度推导：粗粒度 run.progress + 点亮第一个可开始步骤。
                    plan_id = (task.input_payload or {}).get("plan_id")
                    if isinstance(plan_id, str):
                        owner = await session.get(User, task.user_id)
                        if owner is not None:
                            store = SqlPlanningStore(session)
                            await store.emit_run_progress(
                                plan_id,
                                owner,
                                {"step": event.data.get("step", 0)},
                            )
                            await store.auto_advance_todo(plan_id, owner)
                elif event.type in {
                    AgentEventType.COMPLETED,
                    AgentEventType.FAILED,
                    AgentEventType.CANCELLED,
                    AgentEventType.LIMIT_REACHED,
                }:
                    status = event.data.get("status")
                    task.status = str(status or "failed")
                    task.error_code = str(event.data.get("error_code") or "") or None
                    task.output_payload = self._result_view()
                    execution.status = "succeeded" if status == "completed" else (
                        "cancelled" if status == "cancelled" else "failed"
                    )
                    execution.error_code = task.error_code
                    execution.finished_at = now
                    plan_id = (task.input_payload or {}).get("plan_id")
                    owner = await session.get(User, task.user_id)
                    if isinstance(plan_id, str) and owner is not None:
                        target, plan_event = {
                            AgentEventType.COMPLETED: (PlanStatus.COMPLETED, PlanningEventType.RUN_COMPLETED),
                            AgentEventType.CANCELLED: (PlanStatus.CANCELLED, PlanningEventType.RUN_CANCELLED),
                            AgentEventType.FAILED: (PlanStatus.FAILED, PlanningEventType.RUN_FAILED),
                            AgentEventType.LIMIT_REACHED: (PlanStatus.FAILED, PlanningEventType.RUN_FAILED),
                        }[event.type]
                        store = SqlPlanningStore(session)
                        if event.type == AgentEventType.COMPLETED:
                            # 终态兜底：在状态转移（EXECUTING → COMPLETED）之前把剩余
                            # pending / in_progress 步骤推进到 completed。
                            await store.complete_todos(plan_id, owner)
                        plan_snapshot = await store.get_plan(plan_id, owner)
                        await store._transition(
                            plan_id,
                            owner,
                            plan_snapshot.revision,
                            target,
                            plan_event,
                            expected=PlanStatus.EXECUTING,
                        )
                await session.commit()
        except Exception:
            logger.exception("failed to persist Agent execution event task_id=%s", self._task_id)
