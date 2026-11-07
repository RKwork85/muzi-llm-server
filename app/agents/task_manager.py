"""进程内任务表。重启后不保留，也不能跨进程查询。"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

from app.agents.events import EventPublisher
from app.agents.loop import LoopResult
from app.agents.models import TERMINAL_TASK_STATUSES, TaskStatus, UsageSnapshot
from app.core.exceptions import AgentTaskLimitError, AgentTaskNotFoundError


@dataclass
class TaskRecord:
    task_id: str
    request_id: str
    status: TaskStatus = TaskStatus.CREATED
    answer: str | None = None
    steps: int = 0
    tool_calls: int = 0
    error_code: str | None = None
    usage: UsageSnapshot | None = None
    artifacts: list[dict[str, object]] | None = None
    worker: object | None = None
    publisher: EventPublisher | None = None
    finished_at: float | None = None
    _terminal: bool = field(default=False, repr=False)

    def view(self) -> dict[str, object]:
        usage = None
        if self.usage is not None:
            usage = self.usage.model_dump()
        return {
            "task_id": self.task_id,
            "request_id": self.request_id,
            "status": self.status.value,
            "answer": self.answer,
            "steps": self.steps,
            "tool_calls": self.tool_calls,
            "error_code": self.error_code,
            "usage": usage,
        } | ({"artifacts": self.artifacts} if self.artifacts else {})


class TaskManager:
    def __init__(self, *, max_active: int, ttl_seconds: float) -> None:
        self._max_active = max_active
        self._ttl = ttl_seconds
        self._tasks: dict[str, TaskRecord] = {}

    def cleanup(self, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        expired = [
            task_id
            for task_id, record in self._tasks.items()
            if record.finished_at is not None and current - record.finished_at >= self._ttl
        ]
        for task_id in expired:
            del self._tasks[task_id]

    def create(self, request_id: str) -> TaskRecord:
        self.cleanup()
        if self.active_count() >= self._max_active:
            raise AgentTaskLimitError()
        task_id = secrets.token_urlsafe(16)
        while task_id in self._tasks:
            task_id = secrets.token_urlsafe(16)
        record = TaskRecord(task_id=task_id, request_id=request_id)
        self._tasks[task_id] = record
        return record

    def get(self, task_id: str) -> TaskRecord:
        self.cleanup()
        record = self._tasks.get(task_id)
        if record is None:
            raise AgentTaskNotFoundError()
        return record

    def active_count(self) -> int:
        return sum(1 for record in self._tasks.values() if record.status not in TERMINAL_TASK_STATUSES)

    def records(self) -> list[TaskRecord]:
        return list(self._tasks.values())

    def mark_running(self, record: TaskRecord) -> None:
        if record._terminal or record.status != TaskStatus.CREATED:
            return
        record.status = TaskStatus.RUNNING

    def finish(self, record: TaskRecord, result: LoopResult) -> bool:
        if record._terminal:
            return False
        record._terminal = True
        record.status = result.status
        record.answer = result.answer
        record.steps = result.steps
        record.tool_calls = result.tool_calls
        record.error_code = result.error_code
        record.usage = result.usage
        record.artifacts = result.artifacts
        record.finished_at = time.monotonic()
        return True

    def finish_cancelled(self, record: TaskRecord) -> bool:
        if record._terminal:
            return False
        record._terminal = True
        record.status = TaskStatus.CANCELLED
        record.error_code = "AGENT_CANCELLED"
        record.finished_at = time.monotonic()
        return True

    def finish_failed(self, record: TaskRecord, code: str) -> bool:
        if record._terminal:
            return False
        record._terminal = True
        record.status = TaskStatus.FAILED
        record.error_code = code
        record.finished_at = time.monotonic()
        return True
