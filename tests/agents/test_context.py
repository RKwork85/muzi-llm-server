"""ContextManager 的消息顺序、预算和工具组。"""

from __future__ import annotations

import pytest

from app.agents.context import ContextError, ContextManager
from app.agents.policy import ExecutionPolicy, default_server_policy
from app.agents.provider_adapter import NormalizedCompletion, NormalizedToolCall
from app.tools.registry import build_default_registry


def _policy(**overrides: object) -> ExecutionPolicy:
    data = default_server_policy(build_default_registry()).model_dump()
    data.update(overrides)
    return ExecutionPolicy.model_validate(data)


def _completion(
    *,
    content: str | None = "",
    calls: list[NormalizedToolCall] | None = None,
    reasoning: str | None = None,
    finish: str = "stop",
) -> NormalizedCompletion:
    return NormalizedCompletion(
        content=content,
        reasoning_content=reasoning,
        tool_calls=calls or [],
        finish_reason=finish,
        usage=None,
    )


def test_empty_history_keeps_system_and_user() -> None:
    context = ContextManager(system_prompt="系统约束", user_message="当前问题", policy=_policy())
    messages = context.render()
    assert [item["role"] for item in messages] == ["system", "user"]
    assert messages[0]["content"] == "系统约束"
    assert messages[1]["content"] == "当前问题"


def test_tool_group_stays_paired_and_reasoning_stays_internal() -> None:
    context = ContextManager(system_prompt="系统", user_message="问题", policy=_policy())
    context.append_assistant(
        _completion(
            calls=[NormalizedToolCall("call_1", "calculator", '{"expression":"1+1"}')],
            reasoning="secret-thought",
            finish="tool_calls",
        )
    )
    context.append_tool_result("call_1", '{"result":"2"}')
    rendered = context.render()
    assert [item["role"] for item in rendered] == ["system", "user", "assistant", "tool"]
    assert rendered[2]["reasoning_content"] == "secret-thought"
    assert rendered[3]["tool_call_id"] == "call_1"
    public = context.public_messages()
    assert all("reasoning_content" not in item for item in public)
    assert "secret-thought" not in str(public)


def test_trim_drops_whole_tool_group_and_keeps_latest() -> None:
    context = ContextManager(
        system_prompt="系统",
        user_message="问题",
        policy=_policy(max_context_messages=4),
    )
    for index in (1, 2):
        context.append_assistant(
            _completion(
                calls=[NormalizedToolCall(f"call_{index}", "calculator", "{}")],
                finish="tool_calls",
            )
        )
        context.append_tool_result(f"call_{index}", f"result-{index}")
    messages = context.render()
    assert len(messages) <= 4
    assert messages[0]["role"] == "system"
    assert messages[1]["content"] == "问题"
    assert messages[-1]["content"] == "result-2"
    assert "result-1" not in str(messages)
    roles = [item["role"] for item in messages]
    assert roles.count("assistant") == roles.count("tool")


def test_prefix_over_budget_fails_instead_of_dropping_user() -> None:
    context = ContextManager(
        system_prompt="系统" * 80,
        user_message="问题" * 80,
        policy=_policy(max_context_chars=40),
    )
    with pytest.raises(ContextError) as caught:
        context.render()
    assert caught.value.code == "AGENT_CONTEXT_LIMIT_REACHED"


def test_orphan_and_unfinished_tool_calls_fail() -> None:
    context = ContextManager(system_prompt="系统", user_message="问题", policy=_policy())
    with pytest.raises(ContextError) as orphan:
        context.append_tool_result("call_missing", "x")
    assert orphan.value.code == "AGENT_CONTEXT_INVALID"
    context.append_assistant(
        _completion(calls=[NormalizedToolCall("call_1", "calculator", "{}")], finish="tool_calls")
    )
    with pytest.raises(ContextError) as unfinished:
        context.render()
    assert unfinished.value.code == "AGENT_CONTEXT_INVALID"


def test_tool_result_must_match_call_order() -> None:
    context = ContextManager(system_prompt="系统", user_message="问题", policy=_policy())
    context.append_assistant(
        _completion(
            calls=[
                NormalizedToolCall("call_a", "calculator", "{}"),
                NormalizedToolCall("call_b", "get_current_time", "{}"),
            ],
            finish="tool_calls",
        )
    )
    with pytest.raises(ContextError) as caught:
        context.append_tool_result("call_b", "late")
    assert caught.value.code == "AGENT_CONTEXT_INVALID"
