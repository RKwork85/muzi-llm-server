"""任务状态、取消、隔离，以及同步工具线程槽。"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.agents.executor import ToolExecutor
from app.agents.loop import LoopResult
from app.agents.models import AgentRequest, TaskStatus
from app.agents.policy import RunClock, default_server_policy
from app.agents.runtime import AgentRuntime
from app.agents.task_manager import TaskManager
from app.core.exceptions import AgentTaskLimitError, AgentTaskNotFoundError
from app.services.prompt_service import PromptService
from app.tools.registry import ToolRegistry, ToolSpec, build_default_registry
from tests.agents.test_executor import TextArgs, _clock, _executor, _policy, _registry_of, _spec
from tests.conftest import make_settings
from tests.fakes import FakeProvider, make_result


def _completed(answer: str = "ok") -> LoopResult:
    return LoopResult(TaskStatus.COMPLETED, answer, 1, 0, None, None)


def test_terminal_state_is_written_once() -> None:
    manager = TaskManager(max_active=2, ttl_seconds=300)
    record = manager.create("req")
    manager.mark_running(record)
    assert manager.finish(record, _completed("first")) is True
    assert manager.finish(record, _completed("second")) is False
    assert manager.finish_cancelled(record) is False
    assert record.answer == "first"
    assert record.status is TaskStatus.COMPLETED


def test_cancel_after_terminal_is_idempotent_and_ttl_removes_tasks() -> None:
    manager = TaskManager(max_active=1, ttl_seconds=10)
    record = manager.create("req")
    manager.mark_running(record)
    with pytest.raises(AgentTaskLimitError):
        manager.create("req-2")
    assert manager.finish_cancelled(record) is True
    assert manager.finish_cancelled(record) is False
    record.finished_at = time.monotonic() - 11
    manager.cleanup()
    with pytest.raises(AgentTaskNotFoundError):
        manager.get(record.task_id)
    assert manager.create("req-3").request_id == "req-3"


@pytest.mark.asyncio
async def test_runs_are_isolated_and_duplicate_cancel_stops_once() -> None:
    provider = FakeProvider()

    async def scripted(request):
        provider.calls.append(request)
        text = request.messages[-1]["content"]
        if "ALPHA" in text:
            return make_result("答案-ALPHA")
        return make_result("答案-BETA")

    provider.create_chat_completion = scripted  # type: ignore[method-assign]
    runtime = AgentRuntime(
        settings=make_settings(tool_timeout_seconds=2),
        provider=provider,
        prompts=PromptService(),
        registry=build_default_registry(),
    )
    try:
        first = await runtime.start(AgentRequest(message="ALPHA 问题", prompt_name="calculator"), "req-a")
        second = await runtime.start(AgentRequest(message="BETA 问题", prompt_name="calculator"), "req-b")
        views = []
        for task_id in (first["task_id"], second["task_id"]):
            for _ in range(50):
                view = runtime.get(str(task_id))
                if view["status"] in {"completed", "failed", "cancelled", "limit_reached"}:
                    views.append(view)
                    break
                await asyncio.sleep(0.01)
        assert {item["answer"] for item in views} == {"答案-ALPHA", "答案-BETA"}
        assert views[0]["task_id"] != views[1]["task_id"]
        again = await runtime.cancel(str(first["task_id"]))
        assert again["status"] == "completed"
        assert again["answer"] == runtime.get(str(first["task_id"]))["answer"]
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_cancel_propagates_and_late_text_is_not_success() -> None:
    started = asyncio.Event()
    provider = FakeProvider()

    async def blocked(request):
        provider.calls.append(request)
        started.set()
        await asyncio.sleep(30)
        return make_result("LATE_SUCCESS")

    provider.create_chat_completion = blocked  # type: ignore[method-assign]
    runtime = AgentRuntime(
        settings=make_settings(agent_cancel_cleanup_seconds=1),
        provider=provider,
        prompts=PromptService(),
        registry=build_default_registry(),
    )
    try:
        created = await runtime.start(AgentRequest(message="请等一下", prompt_name="calculator"), "req-cancel")
        await asyncio.wait_for(started.wait(), timeout=2)
        cancelled = await runtime.cancel(str(created["task_id"]))
        assert cancelled["status"] == "cancelled"
        assert cancelled["error_code"] == "AGENT_CANCELLED"
        assert cancelled["answer"] != "LATE_SUCCESS"
        repeated = await runtime.cancel(str(created["task_id"]))
        assert repeated["status"] == "cancelled"
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_sync_pool_keeps_slot_until_thread_returns() -> None:
    state = {"current": 0, "peak": 0}
    lock = threading.Lock()

    def slow(_: TextArgs) -> str:
        with lock:
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
        time.sleep(0.35)
        with lock:
            state["current"] -= 1
        return "LATE"

    registry = _registry_of(_spec("slow", slow))
    policy = _policy(allowed_tools=("slow",), tool_timeout_seconds=0.05, max_wall_time_seconds=30)
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-tool")
    executor = _executor(registry, ("slow",))
    executor.bind_sync_executor(pool)
    try:
        results = await asyncio.gather(
            *[
                executor.execute(
                    tool_call_id=f"call_{index}",
                    tool_name="slow",
                    arguments="{}",
                    allowlist=("slow",),
                    clock=_clock(policy),
                )
                for index in range(3)
            ]
        )
        assert [item.status.value for item in results] == ["timed_out", "timed_out", "timed_out"]
        assert all("LATE" not in item.content for item in results)
        assert state["peak"] == 1
        assert state["current"] == 1
        for _ in range(40):
            if state["current"] == 0:
                break
            await asyncio.sleep(0.05)
        assert state["current"] == 0
        assert state["peak"] == 1
    finally:
        pool.shutdown(wait=True, cancel_futures=False)


def test_direct_executor_import_used_by_runtime_pool() -> None:
    registry = build_default_registry()
    policy = default_server_policy(registry)
    executor = ToolExecutor(registry, policy.allowed_tools)
    assert executor._sync_executor is None
    clock = RunClock.start(policy)
    assert clock.policy.allowed_tools == policy.allowed_tools
