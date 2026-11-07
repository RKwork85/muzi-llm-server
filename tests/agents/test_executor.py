"""ToolExecutor 的权限、参数、超时、取消和结果隔离。"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from app.agents.executor import ToolExecutor
from app.agents.models import ToolExecutionStatus
from app.agents.policy import ExecutionPolicy, RunClock, default_server_policy
from app.tools.calculator import CalculatorArgs, calculator_handler
from app.tools.clock import EmptyArgs, current_time_handler
from app.tools.registry import ToolRegistry, ToolSpec, build_default_registry


class TextArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = "ok"


def _policy(**overrides: object) -> ExecutionPolicy:
    base = default_server_policy(build_default_registry())
    data = base.model_dump()
    data.update(overrides)
    return ExecutionPolicy.model_validate(data)


def _clock(policy: ExecutionPolicy, *, started: float | None = None) -> RunClock:
    return RunClock.start(policy, now=started)


def _registry_of(*specs: ToolSpec) -> ToolRegistry:
    registry = ToolRegistry()
    for spec in specs:
        registry.register(spec)
    return registry


def _spec(name: str, handler: Any, args_model: type[BaseModel] = TextArgs) -> ToolSpec:
    return ToolSpec(
        name=name,
        description="测试工具",
        args_model=args_model,
        handler=handler,
        risk_level="low",
    )


def _executor(registry: ToolRegistry, allowed: tuple[str, ...]) -> ToolExecutor:
    return ToolExecutor(registry, allowed)


@pytest.mark.asyncio
async def test_reuses_calculator_and_clock_handlers_once() -> None:
    registry = build_default_registry()
    policy = default_server_policy(registry)
    executor = _executor(registry, policy.allowed_tools)
    calls = {"calculator": 0, "clock": 0}
    original_calc = calculator_handler
    original_clock = current_time_handler

    def counting_calc(args: CalculatorArgs) -> str:
        calls["calculator"] += 1
        return original_calc(args)

    def counting_clock(args: EmptyArgs) -> str:
        calls["clock"] += 1
        return original_clock(args)

    wrapped = _registry_of(
        _spec("calculator", counting_calc, CalculatorArgs),
        _spec("get_current_time", counting_clock, EmptyArgs),
    )
    executor = _executor(wrapped, policy.allowed_tools)
    calc = await executor.execute(
        tool_call_id="call_calc",
        tool_name="calculator",
        arguments='{"expression":"23 * 19"}',
        allowlist=policy.allowed_tools,
        clock=_clock(policy),
        request_id="req-1",
    )
    clock_result = await executor.execute(
        tool_call_id="call_time",
        tool_name="get_current_time",
        arguments="{}",
        allowlist=policy.allowed_tools,
        clock=_clock(policy),
    )
    calc_body = json.loads(calc.content)
    clock_body = json.loads(clock_result.content)
    assert calc.status is ToolExecutionStatus.SUCCEEDED
    assert calc.error_code is None
    assert calc_body["source"] == "server_tool"
    assert calc_body["result"] == "437"
    assert calls["calculator"] == 1
    assert clock_result.status is ToolExecutionStatus.SUCCEEDED
    assert clock_body["result"].endswith("Z")
    assert calls["clock"] == 1
    again = await executor.execute(
        tool_call_id="call_calc_2",
        tool_name="calculator",
        arguments='{"expression":"23 * 19"}',
        allowlist=policy.allowed_tools,
        clock=_clock(policy),
    )
    assert json.loads(again.content)["result"] == "437"
    assert calls["calculator"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        "",
        "{",
        "null",
        "1",
        '"text"',
        "[1]",
        "NaN",
        "Infinity",
        '{"text":"ok","extra":1}',
        '{"expression":"1+1","code":"print(1)"}',
    ],
)
async def test_invalid_arguments_do_not_call_handler(arguments: str) -> None:
    calls = {"n": 0}

    def handler(_: TextArgs) -> str:
        calls["n"] += 1
        return "ran"

    registry = _registry_of(_spec("echo", handler))
    if arguments.startswith('{"expression"'):
        registry = build_default_registry()
        tool_name = "calculator"
        allowed = ("calculator", "get_current_time")
    else:
        tool_name = "echo"
        allowed = ("echo",)
    policy = _policy(allowed_tools=allowed)
    result = await _executor(registry, allowed).execute(
        tool_call_id="call_bad",
        tool_name=tool_name,
        arguments=arguments,
        allowlist=allowed,
        clock=_clock(policy),
    )
    assert result.status is ToolExecutionStatus.REJECTED
    assert result.error_code == "INVALID_TOOL_ARGUMENTS"
    assert calls["n"] == 0
    body = json.loads(result.content)
    assert body["error"]["code"] == "INVALID_TOOL_ARGUMENTS"
    assert "Traceback" not in result.content


@pytest.mark.asyncio
async def test_unknown_tool_and_allowlists_reject_before_handler() -> None:
    calls = {"n": 0}

    def handler(_: TextArgs) -> str:
        calls["n"] += 1
        return "ran"

    registry = _registry_of(
        _spec("echo", handler),
        _spec("calculator", calculator_handler, CalculatorArgs),
    )
    policy = _policy(allowed_tools=("calculator",))
    executor = _executor(registry, ("calculator",))
    unknown = await executor.execute(
        tool_call_id="call_unknown",
        tool_name="shell",
        arguments="{}",
        allowlist=("calculator",),
        clock=_clock(policy),
    )
    outside_server = await executor.execute(
        tool_call_id="call_echo",
        tool_name="echo",
        arguments='{"text":"secret-arg"}',
        allowlist=("echo", "calculator"),
        clock=_clock(policy),
    )
    outside_request = await _executor(registry, ("echo", "calculator")).execute(
        tool_call_id="call_echo_2",
        tool_name="echo",
        arguments='{"text":"ok"}',
        allowlist=("calculator",),
        clock=_clock(_policy(allowed_tools=("calculator", "echo"))),
    )
    assert unknown.status is ToolExecutionStatus.REJECTED
    assert unknown.error_code == "TOOL_NOT_REGISTERED"
    assert outside_server.error_code == "TOOL_NOT_ALLOWED"
    assert outside_request.error_code == "TOOL_NOT_ALLOWED"
    assert calls["n"] == 0
    assert "secret-arg" not in outside_server.content


@pytest.mark.asyncio
async def test_server_allowlist_snapshot_cannot_be_expanded() -> None:
    registry = build_default_registry()
    mutable = {"calculator"}
    executor = ToolExecutor(registry, mutable)
    mutable.add("get_current_time")
    policy = _policy(allowed_tools=("calculator", "get_current_time"))
    result = await executor.execute(
        tool_call_id="call_time",
        tool_name="get_current_time",
        arguments="{}",
        allowlist=policy.allowed_tools,
        clock=_clock(policy),
    )
    assert result.error_code == "TOOL_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_deadline_blocks_handler_before_start() -> None:
    calls = {"n": 0}

    def handler(_: CalculatorArgs) -> str:
        calls["n"] += 1
        return "ran"

    registry = _registry_of(_spec("calculator", handler, CalculatorArgs))
    policy = _policy(allowed_tools=("calculator",), max_wall_time_seconds=30)
    result = await _executor(registry, ("calculator",)).execute(
        tool_call_id="call_deadline",
        tool_name="calculator",
        arguments='{"expression":"1+1"}',
        allowlist=("calculator",),
        clock=_clock(policy, started=0.0),
        now=30.0,
    )
    assert calls["n"] == 0
    assert result.status is ToolExecutionStatus.TIMED_OUT
    assert result.error_code == "TOOL_DEADLINE_EXCEEDED"


@pytest.mark.asyncio
async def test_tool_timeout_does_not_report_late_thread_success() -> None:
    finished = threading.Event()

    def handler(_: TextArgs) -> str:
        time.sleep(0.4)
        finished.set()
        return "LATE_SUCCESS_TOKEN"

    registry = _registry_of(_spec("slow", handler))
    policy = _policy(allowed_tools=("slow",), tool_timeout_seconds=0.05, max_wall_time_seconds=30)
    result = await _executor(registry, ("slow",)).execute(
        tool_call_id="call_slow",
        tool_name="slow",
        arguments="{}",
        allowlist=("slow",),
        clock=_clock(policy),
    )
    assert result.status is ToolExecutionStatus.TIMED_OUT
    assert result.error_code == "TOOL_TIMEOUT"
    assert "LATE_SUCCESS_TOKEN" not in result.content
    assert finished.wait(timeout=2)
    assert result.status is ToolExecutionStatus.TIMED_OUT


@pytest.mark.asyncio
async def test_remaining_wall_time_caps_tool_budget() -> None:
    finished = threading.Event()

    def handler(_: TextArgs) -> str:
        time.sleep(0.4)
        finished.set()
        return "LATE_SUCCESS_TOKEN"

    registry = _registry_of(_spec("slow", handler))
    policy = _policy(allowed_tools=("slow",), tool_timeout_seconds=5, max_wall_time_seconds=30)
    result = await _executor(registry, ("slow",)).execute(
        tool_call_id="call_budget",
        tool_name="slow",
        arguments="{}",
        allowlist=("slow",),
        clock=_clock(policy, started=0.0),
        now=29.95,
    )
    assert result.error_code == "TOOL_TIMEOUT"
    assert "LATE_SUCCESS_TOKEN" not in result.content
    assert finished.wait(timeout=2)


@pytest.mark.asyncio
async def test_handler_exception_is_sanitized_and_not_retried(caplog: pytest.LogCaptureFixture) -> None:
    calls = {"n": 0}

    def handler(_: TextArgs) -> str:
        calls["n"] += 1
        raise RuntimeError("secret-stack-token")

    registry = _registry_of(_spec("boom", handler))
    policy = _policy(allowed_tools=("boom",))
    with caplog.at_level(logging.ERROR, logger="qs_llm"):
        result = await _executor(registry, ("boom",)).execute(
            tool_call_id="call_boom",
            tool_name="boom",
            arguments='{"text":"UNIQUE_ARG_TOKEN"}',
            allowlist=("boom",),
            clock=_clock(policy),
            request_id="req-secret",
        )
    assert calls["n"] == 1
    assert result.status is ToolExecutionStatus.FAILED
    assert result.error_code == "TOOL_EXECUTION_ERROR"
    assert "secret-stack-token" not in result.content
    assert "Traceback" not in result.content
    assert "UNIQUE_ARG_TOKEN" not in result.content
    assert "UNIQUE_ARG_TOKEN" not in caplog.text
    assert "secret-stack-token" not in caplog.text
    assert "boom" in caplog.text
    body = json.loads(result.content)
    assert body["error"]["message"] == "工具执行失败"


@pytest.mark.asyncio
async def test_zero_division_and_value_error_are_safe() -> None:
    registry = build_default_registry()
    policy = default_server_policy(registry)
    divided = await _executor(registry, policy.allowed_tools).execute(
        tool_call_id="call_div",
        tool_name="calculator",
        arguments='{"expression":"1/0"}',
        allowlist=policy.allowed_tools,
        clock=_clock(policy),
    )
    assert divided.status is ToolExecutionStatus.FAILED
    assert divided.error_code == "TOOL_EXECUTION_ERROR"
    assert "ZeroDivisionError" not in divided.content

    def handler(_: TextArgs) -> str:
        raise ValueError("secret-value")

    custom = _registry_of(_spec("echo", handler))
    rejected = await _executor(custom, ("echo",)).execute(
        tool_call_id="call_value",
        tool_name="echo",
        arguments="{}",
        allowlist=("echo",),
        clock=_clock(_policy(allowed_tools=("echo",))),
    )
    assert rejected.status is ToolExecutionStatus.REJECTED
    assert rejected.error_code == "INVALID_TOOL_ARGUMENTS"
    assert "secret-value" not in rejected.content


@pytest.mark.asyncio
async def test_oversized_result_stays_valid_json() -> None:
    def handler(_: TextArgs) -> str:
        return "x" * 5000

    registry = _registry_of(_spec("echo", handler))
    policy = _policy(allowed_tools=("echo",), max_tool_result_chars=180)
    result = await _executor(registry, ("echo",)).execute(
        tool_call_id="call_long",
        tool_name="echo",
        arguments="{}",
        allowlist=("echo",),
        clock=_clock(policy),
    )
    assert result.status is ToolExecutionStatus.SUCCEEDED
    assert len(result.content) <= 180
    body = json.loads(result.content)
    assert body["source"] == "server_tool"
    assert "truncated" in body["result"]
    assert "x" * 5000 not in result.content


@pytest.mark.asyncio
async def test_async_handler_and_sync_handler_does_not_block_loop() -> None:
    async def async_handler(_: TextArgs) -> str:
        await asyncio.sleep(0)
        return "async-ok"

    def sync_handler(_: TextArgs) -> str:
        time.sleep(0.2)
        return "sync-ok"

    registry = _registry_of(
        _spec("async_echo", async_handler),
        _spec("sync_echo", sync_handler),
    )
    policy = _policy(allowed_tools=("async_echo", "sync_echo"), tool_timeout_seconds=2)
    executor = _executor(registry, policy.allowed_tools)
    async_result = await executor.execute(
        tool_call_id="call_async",
        tool_name="async_echo",
        arguments="{}",
        allowlist=policy.allowed_tools,
        clock=_clock(policy),
    )
    ticks = {"n": 0}

    async def ticker() -> None:
        while ticks["n"] < 4:
            ticks["n"] += 1
            await asyncio.sleep(0.02)

    sync_task = asyncio.create_task(
        executor.execute(
            tool_call_id="call_sync",
            tool_name="sync_echo",
            arguments="{}",
            allowlist=policy.allowed_tools,
            clock=_clock(policy),
        )
    )
    await ticker()
    sync_result = await sync_task
    assert json.loads(async_result.content)["result"] == "async-ok"
    assert json.loads(sync_result.content)["result"] == "sync-ok"
    assert ticks["n"] == 4


@pytest.mark.asyncio
async def test_concurrent_calls_keep_their_own_results() -> None:
    def make_handler(token: str):
        def handler(_: TextArgs) -> str:
            time.sleep(0.25)
            return token

        return handler

    registry = _registry_of(
        _spec("tool_a", make_handler("RESULT_A")),
        _spec("tool_b", make_handler("RESULT_B")),
    )
    policy = _policy(allowed_tools=("tool_a", "tool_b"), tool_timeout_seconds=2)
    executor = _executor(registry, policy.allowed_tools)
    started = time.perf_counter()
    left, right = await asyncio.gather(
        executor.execute(
            tool_call_id="call_a",
            tool_name="tool_a",
            arguments="{}",
            allowlist=policy.allowed_tools,
            clock=_clock(policy),
        ),
        executor.execute(
            tool_call_id="call_b",
            tool_name="tool_b",
            arguments="{}",
            allowlist=policy.allowed_tools,
            clock=_clock(policy),
        ),
    )
    elapsed = time.perf_counter() - started
    assert elapsed < 0.45
    assert left.tool_call_id == "call_a"
    assert right.tool_call_id == "call_b"
    assert json.loads(left.content)["result"] == "RESULT_A"
    assert json.loads(right.content)["result"] == "RESULT_B"
    assert left.status is ToolExecutionStatus.SUCCEEDED
    assert right.status is ToolExecutionStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_cancellation_discards_thread_result_and_spares_peer() -> None:
    started = threading.Event()
    finished = threading.Event()

    def slow(_: TextArgs) -> str:
        started.set()
        time.sleep(0.8)
        finished.set()
        return "CANCELLED_SUCCESS_TOKEN"

    def fast(_: TextArgs) -> str:
        return "PEER_OK"

    registry = _registry_of(_spec("slow", slow), _spec("fast", fast))
    policy = _policy(allowed_tools=("fast", "slow"), tool_timeout_seconds=5)
    executor = _executor(registry, policy.allowed_tools)
    slow_task = asyncio.create_task(
        executor.execute(
            tool_call_id="call_slow",
            tool_name="slow",
            arguments="{}",
            allowlist=policy.allowed_tools,
            clock=_clock(policy),
        )
    )
    assert await asyncio.to_thread(started.wait, 2)
    peer = await executor.execute(
        tool_call_id="call_fast",
        tool_name="fast",
        arguments="{}",
        allowlist=policy.allowed_tools,
        clock=_clock(policy),
    )
    slow_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await slow_task
    assert slow_task.cancelled()
    assert finished.wait(timeout=2)
    assert peer.status is ToolExecutionStatus.SUCCEEDED
    assert json.loads(peer.content)["result"] == "PEER_OK"
    assert slow_task.cancelled()


@pytest.mark.asyncio
async def test_long_tool_name_that_cannot_fit_is_failed_json() -> None:
    def handler(_: TextArgs) -> str:
        return "x" * 500

    name = "n" * 64
    registry = _registry_of(_spec(name, handler))
    policy = _policy(allowed_tools=(name,), max_tool_result_chars=96)
    result = await _executor(registry, (name,)).execute(
        tool_call_id="call_long_name",
        tool_name=name,
        arguments="{}",
        allowlist=(name,),
        clock=_clock(policy),
    )
    assert result.status is ToolExecutionStatus.FAILED
    assert result.error_code == "TOOL_RESULT_TOO_LARGE"
    body = json.loads(result.content)
    assert body["error"]["code"] == "TOOL_RESULT_TOO_LARGE"
    assert "x" * 100 not in result.content


@pytest.mark.asyncio
async def test_short_name_truncation_stays_loadable_json() -> None:
    def handler(_: TextArgs) -> str:
        return "值" * 400

    registry = _registry_of(_spec("echo", handler))
    policy = _policy(allowed_tools=("echo",), max_tool_result_chars=96)
    result = await _executor(registry, ("echo",)).execute(
        tool_call_id="call_unicode_cut",
        tool_name="echo",
        arguments="{}",
        allowlist=("echo",),
        clock=_clock(policy),
    )
    assert result.status is ToolExecutionStatus.SUCCEEDED
    assert len(result.content) <= 96
    body = json.loads(result.content)
    assert body["result"].endswith("...[truncated]")
    assert "值" in body["result"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        '{"text": NaN}',
        '{"text": Infinity}',
        '{"text": -Infinity}',
        '{"a":' * 40 + "1" + "}" * 40,
        json.dumps({"text": "x" * 9000}),
    ],
)
async def test_deep_long_and_nonfinite_arguments_are_rejected(arguments: str) -> None:
    calls = {"n": 0}

    def handler(_: TextArgs) -> str:
        calls["n"] += 1
        return "ran"

    registry = _registry_of(_spec("echo", handler))
    policy = _policy(allowed_tools=("echo",))
    result = await _executor(registry, ("echo",)).execute(
        tool_call_id="call_limit",
        tool_name="echo",
        arguments=arguments,
        allowlist=("echo",),
        clock=_clock(policy),
    )
    assert result.status is ToolExecutionStatus.REJECTED
    assert result.error_code == "INVALID_TOOL_ARGUMENTS"
    assert calls["n"] == 0
    json.loads(result.content)
    assert "Traceback" not in result.content


def test_executor_does_not_use_phase1_service_or_dynamic_execution() -> None:
    source = Path(__file__).resolve().parents[2].joinpath("app", "agents", "executor.py").read_text(
        encoding="utf-8"
    )
    assert "ToolService" not in source
    assert "eval(" not in source
    assert "exec(" not in source
