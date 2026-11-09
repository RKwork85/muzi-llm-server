"""Process-local cancellation bridge for active Chat SSE requests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class ActiveChatRun:
    owner_id: str
    task: asyncio.Task[object]


class ChatRunRegistry:
    def __init__(self) -> None:
        self._runs: dict[str, ActiveChatRun] = {}
        self._lock = asyncio.Lock()

    async def register(self, run_id: str, owner_id: str) -> None:
        task = asyncio.current_task()
        if task is None:
            return
        async with self._lock:
            self._runs[run_id] = ActiveChatRun(owner_id=owner_id, task=task)

    async def unregister(self, run_id: str) -> None:
        async with self._lock:
            self._runs.pop(run_id, None)

    async def cancel(self, run_id: str, owner_id: str) -> bool:
        async with self._lock:
            run = self._runs.get(run_id)
            if run is None or run.owner_id != owner_id:
                return False
            run.task.cancel()
            return True


chat_run_registry = ChatRunRegistry()
