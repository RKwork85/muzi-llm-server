"""有界事件队列。慢消费者不能挡住 Agent 循环。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.agents.models import AgentEventType, EventEnvelope

_TERMINAL = frozenset(
    {
        AgentEventType.COMPLETED,
        AgentEventType.FAILED,
        AgentEventType.CANCELLED,
        AgentEventType.LIMIT_REACHED,
    }
)
_ALLOWED_FIELDS = frozenset(
    {
        "step",
        "tool_name",
        "status",
        "error_code",
        "finish_reason",
        "dropped_events",
        "category",
        "artifact_id",
        "artifact_type",
        "artifact_size",
        "artifact_count",
    }
)
_MAX_VALUE = 120


class EventPublisher:
    def __init__(self, *, request_id: str, task_id: str, max_queue_size: int) -> None:
        self._request_id = request_id
        self._task_id = task_id
        self._limit = max(1, max_queue_size)
        self._sequence = 0
        self.dropped = 0
        self._retained: list[EventEnvelope] = []
        self._subscribers: list[asyncio.Queue[EventEnvelope]] = []
        self._closed = False

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        if self._closed:
            return
        kind = AgentEventType(event_type)
        self._sequence += 1
        envelope = EventEnvelope(
            request_id=self._request_id,
            task_id=self._task_id,
            sequence=self._sequence,
            timestamp=_timestamp(),
            type=kind,
            data=_sanitize(data or {}),
        )
        self._retain(envelope)
        for subscriber in list(self._subscribers):
            self._offer(subscriber, envelope)

    def subscribe(self) -> asyncio.Queue[EventEnvelope]:
        queue: asyncio.Queue[EventEnvelope] = asyncio.Queue(maxsize=self._limit)
        for item in self._retained:
            self._offer(queue, item)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[EventEnvelope]) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def close(self) -> None:
        self._closed = True
        self._subscribers.clear()

    def _retain(self, envelope: EventEnvelope) -> None:
        self._retained.append(envelope)
        while len(self._retained) > self._limit:
            dropped = _drop_oldest_progress(self._retained)
            if not dropped:
                self._retained.pop(0)
            self.dropped += 1

    def _offer(self, queue: asyncio.Queue[EventEnvelope], envelope: EventEnvelope) -> None:
        if queue.full() and envelope.type in _TERMINAL:
            _discard_progress(queue)
        try:
            queue.put_nowait(envelope)
        except asyncio.QueueFull:
            self.dropped += 1


def _sanitize(data: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in data.items():
        if key not in _ALLOWED_FIELDS:
            continue
        if isinstance(value, str):
            cleaned[key] = value[:_MAX_VALUE]
        elif isinstance(value, int | float) and not isinstance(value, bool):
            cleaned[key] = value
        elif value is None:
            cleaned[key] = ""
    return cleaned


def _drop_oldest_progress(items: list[EventEnvelope]) -> bool:
    for index, item in enumerate(items):
        if item.type not in _TERMINAL:
            del items[index]
            return True
    return False


def _discard_progress(queue: asyncio.Queue[EventEnvelope]) -> None:
    kept: list[EventEnvelope] = []
    while not queue.empty():
        item = queue.get_nowait()
        if item.type in _TERMINAL:
            kept.append(item)
    for item in kept:
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            return


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
