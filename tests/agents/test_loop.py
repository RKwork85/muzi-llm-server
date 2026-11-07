"""AgentLoop 的完成、工具、限额和失败映射。"""

from __future__ import annotations

import time

import pytest

from app.agents.context import ContextManager
from app.agents.executor import ToolExecutor
from app.agents.loop import AgentLoop
from app.agents.models import TaskStatus
from app.agents.policy import ExecutionPolicy, RunClock, server_policy_from_settings
from app.tools.registry import build_default_registry
from tests.conftest import make_settings
from tests.fakes import FakeProvider, make_result, make_tool_call


class MemoryEvents:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def publish(self, event_type: str, data: dict[str, object]) -> None:
        self.events.append((event_type, dict(data)))


def _policy(**overrides: object) -> ExecutionPolicy:
    registry = build_default_registry()
    data = server_policy_from_settings(
        make_settings(tool_timeout_seconds=2, deepseek_timeout_seconds=5),
        registry,
    ).model_dump()
    data.update(overrides)
    return ExecutionPolicy.model_validate(data)


def _loop(
    provider: FakeProvider,
    policy: ExecutionPolicy | None = None,
    *,
    thinking: str = "disabled",
    clock: RunClock | None = None,
) -> tuple[AgentLoop, MemoryEvents]:
    registry = build_default_registry()
    chosen = policy or _policy()
    events = MemoryEvents()
    loop = AgentLoop(
        provider=provider,
        executor=ToolExecutor(registry, chosen.allowed_tools),
        context=ContextManager(system_prompt="系统", user_message="计算 23 * 19", policy=chosen),
        policy=chosen,
        clock=clock or RunClock.start(chosen),
        registry=registry,
        model="deepseek-flash",
        thinking=thinking,
        request_id="req-loop",
        events=events,
    )
    return loop, events


@pytest.mark.asyncio
async def test_plain_answer_completes() -> None:
    provider = FakeProvider()
    provider.push(make_result("最终答案"))
    loop, events = _loop(provider)
    result = await loop.run()
    assert result.status is TaskStatus.COMPLETED
    assert result.answer == "最终答案"
    assert result.error_code is None
    assert provider.calls[0].tools
    assert provider.calls[0].thinking == {"type": "disabled"}
    assert events.events[0][0] == "agent.step.started"


@pytest.mark.asyncio
async def test_single_and_multiple_tools_round_trip() -> None:
    provider = FakeProvider()
    provider.push(
        make_result(
            content=None,
            tool_calls=[make_tool_call(), make_tool_call("call_time", "get_current_time", "{}")],
            finish_reason="tool_calls",
            reasoning="secret-thought",
        )
    )
    provider.push(make_result("两个工具都完成了"))
    loop, _events = _loop(provider)
    result = await loop.run()
    assert result.status is TaskStatus.COMPLETED
    assert result.tool_calls == 2
    follow = provider.calls[1].messages
    assert follow[2]["reasoning_content"] == "secret-thought"
    assert [item["role"] for item in follow[2:]] == ["assistant", "tool", "tool"]
    assert follow[3]["tool_call_id"] == "call_calc"
    assert follow[4]["tool_call_id"] == "call_time"
    assert "437" in follow[3]["content"]
    assert "secret-thought" not in (result.answer or "")


@pytest.mark.asyncio
async def test_unauthorized_tool_is_rejected_and_not_executed_as_success() -> None:
    provider = FakeProvider()
    provider.push(make_result(content=None, tool_calls=[make_tool_call("call_shell", "shell", "{}")], finish_reason="tool_calls"))
    provider.push(make_result("工具不可用"))
    loop, _events = _loop(provider)
    result = await loop.run()
    assert result.status is TaskStatus.COMPLETED
    body = provider.calls[1].messages[-1]["content"]
    assert "TOOL_NOT_REGISTERED" in body
    assert result.answer == "工具不可用"


@pytest.mark.asyncio
async def test_duplicate_tool_ids_do_not_execute() -> None:
    provider = FakeProvider()
    provider.push(
        make_result(
            content=None,
            tool_calls=[make_tool_call("same", "calculator", '{"expression":"1+1"}'), make_tool_call("same", "calculator", '{"expression":"2+2"}')],
            finish_reason="tool_calls",
        )
    )
    loop, _events = _loop(provider)
    result = await loop.run()
    assert result.status is TaskStatus.FAILED
    assert result.error_code == "INVALID_MODEL_RESPONSE"
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_tool_failure_is_returned_to_the_model() -> None:
    provider = FakeProvider()
    provider.push(make_result(content=None, tool_calls=[make_tool_call("call_div", "calculator", '{"expression":"1/0"}')], finish_reason="tool_calls"))
    provider.push(make_result("不能除以零"))
    loop, _events = _loop(provider)
    result = await loop.run()
    assert result.status is TaskStatus.COMPLETED
    assert "ZeroDivisionError" not in provider.calls[1].messages[-1]["content"]
    assert result.answer == "不能除以零"


@pytest.mark.asyncio
async def test_step_and_tool_limits() -> None:
    provider = FakeProvider()
    provider.push(make_result(content=None, tool_calls=[make_tool_call()], finish_reason="tool_calls"))
    provider.push(make_result(content=None, tool_calls=[make_tool_call("call_2", "calculator", '{"expression":"1+1"}')], finish_reason="tool_calls"))
    loop, _events = _loop(provider, _policy(max_steps=1))
    result = await loop.run()
    assert result.status is TaskStatus.LIMIT_REACHED
    assert result.error_code == "AGENT_STEP_LIMIT"
    assert len(provider.calls) == 1

    provider = FakeProvider()
    provider.push(
        make_result(
            content=None,
            tool_calls=[make_tool_call("a", "calculator", '{"expression":"1+1"}'), make_tool_call("b", "calculator", '{"expression":"2+2"}')],
            finish_reason="tool_calls",
        )
    )
    loop, _events = _loop(provider, _policy(max_tool_calls=1, max_tool_calls_per_step=1))
    limited = await loop.run()
    assert limited.status is TaskStatus.LIMIT_REACHED
    assert limited.error_code == "AGENT_TOOL_LIMIT"
    assert limited.tool_calls == 0


@pytest.mark.asyncio
async def test_deadline_prevents_model_call() -> None:
    provider = FakeProvider()
    provider.push(make_result("不应该出现"))
    policy = _policy()
    clock = RunClock.start(policy, now=time.monotonic() - 1000)
    loop, _events = _loop(provider, policy, clock=clock)
    result = await loop.run()
    assert result.status is TaskStatus.LIMIT_REACHED
    assert result.error_code == "AGENT_DEADLINE_EXCEEDED"
    assert provider.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("finish", "code"),
    [
        ("length", "MODEL_OUTPUT_LIMIT"),
        ("content_filter", "MODEL_CONTENT_FILTER"),
        ("insufficient_system_resource", "MODEL_RESOURCE_EXHAUSTED"),
        ("aborted", "MODEL_ABORTED"),
        ("mystery", "MODEL_FINISH_UNKNOWN"),
    ],
)
async def test_abnormal_finish_is_not_completed(finish: str, code: str) -> None:
    provider = FakeProvider()
    provider.push(make_result("被截断的文本", finish_reason=finish))
    loop, _events = _loop(provider)
    result = await loop.run()
    assert result.status is TaskStatus.FAILED
    assert result.error_code == code
    assert result.answer is None


@pytest.mark.asyncio
async def test_empty_stop_fails() -> None:
    provider = FakeProvider()
    provider.push(make_result("   "))
    loop, _events = _loop(provider)
    result = await loop.run()
    assert result.status is TaskStatus.FAILED
    assert result.error_code == "EMPTY_MODEL_RESPONSE"


@pytest.mark.asyncio
async def test_enabled_thinking_without_reasoning_stops() -> None:
    provider = FakeProvider()
    provider.push(make_result(content=None, tool_calls=[make_tool_call()], finish_reason="tool_calls", reasoning=None))
    loop, _events = _loop(provider, thinking="enabled")
    result = await loop.run()
    assert result.status is TaskStatus.FAILED
    assert result.error_code == "MODEL_REASONING_MISSING"
    assert len(provider.calls) == 1
