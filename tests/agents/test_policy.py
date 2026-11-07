"""ExecutionPolicy 的默认值、收紧合并和越权拒绝。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agents.models import AgentEventType, TaskStatus, ToolExecutionStatus
from app.agents.policy import (
    DEFAULT_MAX_CONTEXT_CHARS,
    DEFAULT_MAX_CONTEXT_MESSAGES,
    DEFAULT_MAX_EVENT_QUEUE_SIZE,
    DEFAULT_MAX_STEPS,
    DEFAULT_MAX_TOOL_CALLS,
    DEFAULT_MAX_TOOL_CALLS_PER_STEP,
    DEFAULT_MAX_TOOL_RESULT_CHARS,
    DEFAULT_MAX_WALL_TIME_SECONDS,
    DEFAULT_MODEL_TIMEOUT_SECONDS,
    DEFAULT_TOOL_TIMEOUT_SECONDS,
    HARD_MAX_STEPS,
    ExecutionPolicy,
    PolicyError,
    RunClock,
    default_server_policy,
    merge_execution_policy,
    parse_agent_request,
    registered_tool_view,
    server_policy_from_settings,
)
from app.tools.registry import build_default_registry
from tests.conftest import make_settings


def _request(payload: dict[str, object] | None = None):
    body: dict[str, object] = {
        "message": "计算 23 * 19",
        "prompt_name": "calculator",
    }
    if payload:
        body.update(payload)
    return parse_agent_request(body)


def test_default_policy_matches_documented_limits() -> None:
    policy = default_server_policy(build_default_registry())
    assert policy.max_steps == DEFAULT_MAX_STEPS == 11
    assert policy.max_tool_calls == DEFAULT_MAX_TOOL_CALLS == 10
    assert policy.max_tool_calls_per_step == DEFAULT_MAX_TOOL_CALLS_PER_STEP == 3
    assert policy.max_wall_time_seconds == DEFAULT_MAX_WALL_TIME_SECONDS == 30
    assert policy.model_timeout_seconds == DEFAULT_MODEL_TIMEOUT_SECONDS == 60
    assert policy.tool_timeout_seconds == DEFAULT_TOOL_TIMEOUT_SECONDS == 2
    assert policy.max_context_messages == DEFAULT_MAX_CONTEXT_MESSAGES == 20
    assert policy.max_context_chars == DEFAULT_MAX_CONTEXT_CHARS == 32000
    assert policy.max_tool_result_chars == DEFAULT_MAX_TOOL_RESULT_CHARS == 4000
    assert policy.max_event_queue_size == DEFAULT_MAX_EVENT_QUEUE_SIZE == 100
    assert policy.allowed_tools == ("calculator", "get_current_time")
    assert policy.max_steps <= HARD_MAX_STEPS


def test_settings_policy_uses_phase1_ceilings() -> None:
    settings = make_settings(
        deepseek_timeout_seconds=5,
        tool_timeout_seconds=0.2,
        tool_result_max_chars=1000,
    )
    policy = server_policy_from_settings(settings, build_default_registry())
    assert policy.model_timeout_seconds == 5
    assert policy.tool_timeout_seconds == 0.2
    assert policy.max_tool_result_chars == 1000
    assert policy.max_steps == 11
    view = registered_tool_view(build_default_registry().get("calculator"))
    assert view.enabled is True
    assert view.risk_level == "low"
    assert view.name == "calculator"


def test_omitted_request_keeps_server_values() -> None:
    server = default_server_policy(build_default_registry())
    effective = merge_execution_policy(server, _request())
    assert effective == server
    assert effective is not server


def test_request_can_only_tighten_limits() -> None:
    server = default_server_policy(build_default_registry())
    effective = merge_execution_policy(
        server,
        _request(
            {
                "allowed_tools": ["get_current_time", "calculator"],
                "policy": {
                    "max_steps": 2,
                    "max_tool_calls": 1,
                    "max_tool_calls_per_step": 1,
                    "max_wall_time_seconds": 10,
                    "model_timeout_seconds": 8,
                    "tool_timeout_seconds": 1,
                    "max_context_messages": 6,
                    "max_context_chars": 1000,
                    "max_tool_result_chars": 200,
                    "max_event_queue_size": 20,
                },
            }
        ),
    )
    assert effective.max_steps == 2
    assert effective.max_tool_calls == 1
    assert effective.max_tool_calls_per_step == 1
    assert effective.max_wall_time_seconds == 10
    assert effective.model_timeout_seconds == 8
    assert effective.tool_timeout_seconds == 1
    assert effective.max_context_messages == 6
    assert effective.max_context_chars == 1000
    assert effective.max_tool_result_chars == 200
    assert effective.max_event_queue_size == 20
    assert effective.allowed_tools == ("calculator", "get_current_time")


def test_equal_to_server_ceiling_is_accepted() -> None:
    server = default_server_policy(build_default_registry())
    effective = merge_execution_policy(
        server,
        _request({"policy": {"max_steps": server.max_steps}}),
    )
    assert effective.max_steps == server.max_steps


@pytest.mark.parametrize(
    "policy",
    [
        {"max_steps": 0},
        {"max_steps": -1},
        {"max_tool_calls": 0},
        {"max_wall_time_seconds": 0},
        {"max_wall_time_seconds": -3},
        {"tool_timeout_seconds": 0},
        {"max_tool_result_chars": 95},
        {"max_steps": True},
        {"max_steps": "4"},
    ],
)
def test_zero_negative_and_coerced_limits_are_rejected(policy: dict[str, object]) -> None:
    with pytest.raises(PolicyError) as caught:
        _request({"policy": policy})
    assert caught.value.code == "AGENT_POLICY_INVALID"


def test_request_cannot_raise_server_ceiling() -> None:
    server = server_policy_from_settings(make_settings(), build_default_registry())
    with pytest.raises(PolicyError) as caught:
        merge_execution_policy(
            server,
            _request({"policy": {"tool_timeout_seconds": server.tool_timeout_seconds + 0.1}}),
        )
    assert caught.value.code == "AGENT_POLICY_INVALID"
    assert "tool_timeout_seconds" in caught.value.message


def test_hard_cap_rejects_oversized_server_policy() -> None:
    with pytest.raises(ValidationError):
        ExecutionPolicy(
            max_steps=HARD_MAX_STEPS + 1,
            max_tool_calls=3,
            max_tool_calls_per_step=3,
            max_wall_time_seconds=30,
            model_timeout_seconds=60,
            tool_timeout_seconds=2,
            max_context_messages=20,
            max_context_chars=32000,
            max_tool_result_chars=4000,
            max_event_queue_size=100,
            allowed_tools=("calculator",),
        )


def test_per_step_cannot_exceed_total_tool_calls() -> None:
    server = default_server_policy(build_default_registry())
    with pytest.raises(PolicyError) as caught:
        merge_execution_policy(server, _request({"policy": {"max_tool_calls": 1}}))
    assert caught.value.code == "AGENT_POLICY_INVALID"


def test_unknown_and_unauthorized_tools_are_rejected() -> None:
    server = default_server_policy(build_default_registry())
    with pytest.raises(PolicyError) as unknown:
        merge_execution_policy(server, _request({"allowed_tools": ["shell"]}))
    assert unknown.value.code == "AGENT_TOOL_NOT_ALLOWED"

    calculator_only = server.model_copy(update={"allowed_tools": ("calculator",)})
    with pytest.raises(PolicyError) as narrowed:
        merge_execution_policy(
            calculator_only,
            _request({"allowed_tools": ["get_current_time"]}),
        )
    assert narrowed.value.code == "AGENT_TOOL_NOT_ALLOWED"


def test_empty_allowlist_is_explicit_deny() -> None:
    server = default_server_policy(build_default_registry())
    effective = merge_execution_policy(server, _request({"allowed_tools": []}))
    assert effective.allowed_tools == ()


def test_duplicate_and_illegal_tool_names_are_rejected() -> None:
    server = default_server_policy(build_default_registry())
    with pytest.raises(PolicyError) as duplicate:
        merge_execution_policy(
            server,
            _request({"allowed_tools": ["calculator", "calculator"]}),
        )
    assert duplicate.value.code == "AGENT_POLICY_INVALID"
    with pytest.raises(PolicyError) as illegal:
        merge_execution_policy(server, _request({"allowed_tools": ["../calculator"]}))
    assert illegal.value.code == "AGENT_POLICY_INVALID"


def test_security_fields_and_bad_requests_are_rejected() -> None:
    with pytest.raises(PolicyError):
        _request({"policy": {"expose_reasoning": True}})
    with pytest.raises(PolicyError):
        _request({"thinking": "enabled"})
    with pytest.raises(PolicyError):
        _request({"message": ""})
    with pytest.raises(PolicyError):
        _request({"prompt_name": "../default"})
    with pytest.raises(PolicyError):
        _request({"tools": [{"name": "shell"}]})


def test_merge_is_deterministic_and_isolated_from_later_mutation() -> None:
    server = default_server_policy(build_default_registry())
    request = _request(
        {
            "allowed_tools": ["get_current_time", "calculator"],
            "policy": {"max_steps": 2},
        }
    )
    first = merge_execution_policy(server, request)
    second = merge_execution_policy(server, request)
    assert first.allowed_tools == second.allowed_tools == ("calculator", "get_current_time")
    assert request.policy is not None
    request.policy.max_steps = 1
    assert request.allowed_tools is not None
    request.allowed_tools.append("shell")
    assert first.max_steps == 2
    assert first.allowed_tools == ("calculator", "get_current_time")
    with pytest.raises(ValidationError):
        ExecutionPolicy.model_validate({**first.model_dump(), "max_steps": 100})


def test_run_clock_keeps_wall_model_and_tool_budgets_distinct() -> None:
    policy = default_server_policy(build_default_registry())
    clock = RunClock.start(policy, now=100.0)
    assert clock.remaining(now=100.0) == 30
    assert clock.model_timeout(now=100.0) == 30
    assert clock.tool_timeout(now=100.0) == 2
    assert clock.remaining(now=120.0) == 10
    assert clock.model_timeout(now=120.0) == 10
    assert clock.tool_timeout(now=125.0) == 2
    assert clock.remaining(now=140.0) == -10
    assert clock.tool_timeout(now=140.0) < 0
    assert clock.elapsed(now=90.0) == 0
    assert clock.model_timeout(now=90.0) == 30


def test_domain_status_values_are_closed() -> None:
    assert TaskStatus.LIMIT_REACHED.value == "limit_reached"
    assert {item.value for item in ToolExecutionStatus} == {
        "succeeded",
        "failed",
        "timed_out",
        "rejected",
    }
    assert AgentEventType.STARTED.value == "agent.started"
    assert AgentEventType.LIMIT_REACHED.value == "agent.limit_reached"
