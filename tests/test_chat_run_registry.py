from __future__ import annotations

import asyncio

import pytest

from app.chat.run_registry import ChatRunRegistry


@pytest.mark.asyncio
async def test_chat_run_cancellation_is_owner_scoped_and_interrupts_active_task() -> None:
    registry = ChatRunRegistry()
    registered = asyncio.Event()

    async def active_run() -> None:
        await registry.register("run_12345678", "owner-a")
        registered.set()
        try:
            await asyncio.Event().wait()
        finally:
            await registry.unregister("run_12345678")

    task = asyncio.create_task(active_run())
    await registered.wait()
    assert await registry.cancel("run_12345678", "owner-b") is False
    assert await registry.cancel("run_12345678", "owner-a") is True
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await registry.cancel("run_12345678", "owner-a") is False
