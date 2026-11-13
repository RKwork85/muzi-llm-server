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
    assert await registry.is_active("run_12345678", "owner-a") is False


@pytest.mark.asyncio
async def test_active_check_is_owner_scoped_and_ends_when_unregistered() -> None:
    registry = ChatRunRegistry()
    registered = asyncio.Event()

    async def active_run() -> None:
        await registry.register("run_12345678", "owner-a")
        registered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(active_run())
    await registered.wait()
    assert await registry.is_active("run_12345678", "owner-a") is True
    assert await registry.is_active("run_12345678", "owner-b") is False
    await registry.unregister("run_12345678")
    assert await registry.is_active("run_12345678", "owner-a") is False
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
