"""服务端执行策略。请求只能收紧限制，不能放宽，也不能扩大工具权限。"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.agents.models import AgentRequest, RegisteredToolView, RequestedPolicy
from app.core.config import Settings
from app.tools.registry import ToolRegistry, ToolSpec

_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

HARD_MAX_STEPS = 40
HARD_MAX_TOOL_CALLS = 80
HARD_MAX_TOOL_CALLS_PER_STEP = 12
HARD_MAX_WALL_TIME_SECONDS = 600.0
HARD_MODEL_TIMEOUT_SECONDS = 600.0
HARD_TOOL_TIMEOUT_SECONDS = 60.0
HARD_MAX_CONTEXT_MESSAGES = 60
HARD_MAX_CONTEXT_CHARS = 128000
HARD_MAX_TOOL_RESULT_CHARS = 20000
HARD_MAX_EVENT_QUEUE_SIZE = 500
MIN_TOOL_RESULT_CHARS = 96

DEFAULT_MAX_STEPS = 30
DEFAULT_MAX_TOOL_CALLS = 60
DEFAULT_MAX_TOOL_CALLS_PER_STEP = 8
DEFAULT_MAX_WALL_TIME_SECONDS = 180.0
DEFAULT_MODEL_TIMEOUT_SECONDS = 120.0
DEFAULT_TOOL_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_CONTEXT_MESSAGES = 40
DEFAULT_MAX_CONTEXT_CHARS = 64000
DEFAULT_MAX_TOOL_RESULT_CHARS = 6000
DEFAULT_MAX_EVENT_QUEUE_SIZE = 100

_LIMIT_FIELDS = (
    "max_steps",
    "max_tool_calls",
    "max_tool_calls_per_step",
    "max_wall_time_seconds",
    "model_timeout_seconds",
    "tool_timeout_seconds",
    "max_context_messages",
    "max_context_chars",
    "max_tool_result_chars",
    "max_event_queue_size",
)


class PolicyError(Exception):
    """策略拒绝。message 只包含安全说明，不回显用户正文。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class ExecutionPolicy(BaseModel):
    """一次运行的有效限制。数值上限和工具名单都已经过服务端裁剪。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_steps: int = Field(ge=1, le=HARD_MAX_STEPS)
    max_tool_calls: int = Field(ge=1, le=HARD_MAX_TOOL_CALLS)
    max_tool_calls_per_step: int = Field(ge=1, le=HARD_MAX_TOOL_CALLS_PER_STEP)
    max_wall_time_seconds: float = Field(gt=0, le=HARD_MAX_WALL_TIME_SECONDS)
    model_timeout_seconds: float = Field(gt=0, le=HARD_MODEL_TIMEOUT_SECONDS)
    tool_timeout_seconds: float = Field(gt=0, le=HARD_TOOL_TIMEOUT_SECONDS)
    max_context_messages: int = Field(ge=1, le=HARD_MAX_CONTEXT_MESSAGES)
    max_context_chars: int = Field(ge=1, le=HARD_MAX_CONTEXT_CHARS)
    max_tool_result_chars: int = Field(ge=MIN_TOOL_RESULT_CHARS, le=HARD_MAX_TOOL_RESULT_CHARS)
    max_event_queue_size: int = Field(ge=1, le=HARD_MAX_EVENT_QUEUE_SIZE)
    allowed_tools: tuple[str, ...]

    @field_validator("allowed_tools")
    @classmethod
    def _check_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not _TOOL_NAME.fullmatch(name) for name in value):
            raise ValueError("工具名称不合法")
        if len(value) != len(set(value)):
            raise ValueError("allowed_tools 重复")
        if tuple(sorted(value)) != tuple(value):
            raise ValueError("allowed_tools 必须按名称排序")
        return value

    @model_validator(mode="after")
    def _check_relationships(self) -> ExecutionPolicy:
        if self.max_tool_calls_per_step > self.max_tool_calls:
            raise ValueError("max_tool_calls_per_step 不能大于 max_tool_calls")
        return self


@dataclass(frozen=True)
class RunClock:
    """用单调时钟区分总时限、模型调用预算和单工具预算。"""

    started_monotonic: float
    policy: ExecutionPolicy

    @classmethod
    def start(cls, policy: ExecutionPolicy, *, now: float | None = None) -> RunClock:
        return cls(
            started_monotonic=time.monotonic() if now is None else now,
            policy=policy,
        )

    def elapsed(self, now: float | None = None) -> float:
        current = time.monotonic() if now is None else now
        if current < self.started_monotonic:
            return 0.0
        return current - self.started_monotonic

    def remaining(self, now: float | None = None) -> float:
        return self.policy.max_wall_time_seconds - self.elapsed(now)

    def model_timeout(self, now: float | None = None) -> float:
        """未来 Runtime 用它约束 Provider，不修改 Provider 自身的超时配置。"""
        return min(self.policy.model_timeout_seconds, self.remaining(now))

    def tool_timeout(self, now: float | None = None) -> float:
        return min(self.policy.tool_timeout_seconds, self.remaining(now))


def low_risk_tool_names(registry: ToolRegistry) -> tuple[str, ...]:
    names = [spec.name for spec in registry.all_tools() if spec.risk_level == "low"]
    return tuple(sorted(names))


def basic_tool_names(registry: ToolRegistry) -> tuple[str, ...]:
    """默认请求只暴露 basic toolset。其它 low 工具必须由 toolsets 显式选中。"""
    names = [
        spec.name
        for spec in registry.all_tools()
        if spec.risk_level == "low" and spec.toolset == "basic"
    ]
    return tuple(sorted(names))


def registered_tool_view(spec: ToolSpec) -> RegisteredToolView:
    return RegisteredToolView(
        name=spec.name,
        description=spec.description,
        risk_level=spec.risk_level,
        enabled=spec.risk_level == "low",
    )


def default_server_policy(registry: ToolRegistry) -> ExecutionPolicy:
    """计划中的服务端默认值。模型超时和工具限制对齐第一阶段配置默认值。"""
    return ExecutionPolicy(
        max_steps=DEFAULT_MAX_STEPS,
        max_tool_calls=DEFAULT_MAX_TOOL_CALLS,
        max_tool_calls_per_step=DEFAULT_MAX_TOOL_CALLS_PER_STEP,
        max_wall_time_seconds=DEFAULT_MAX_WALL_TIME_SECONDS,
        model_timeout_seconds=DEFAULT_MODEL_TIMEOUT_SECONDS,
        tool_timeout_seconds=DEFAULT_TOOL_TIMEOUT_SECONDS,
        max_context_messages=DEFAULT_MAX_CONTEXT_MESSAGES,
        max_context_chars=DEFAULT_MAX_CONTEXT_CHARS,
        max_tool_result_chars=DEFAULT_MAX_TOOL_RESULT_CHARS,
        max_event_queue_size=DEFAULT_MAX_EVENT_QUEUE_SIZE,
        allowed_tools=basic_tool_names(registry),
    )


def server_policy_from_settings(settings: Settings, registry: ToolRegistry) -> ExecutionPolicy:
    """用第一阶段已有配置作为天花板，不新增环境变量。"""
    return ExecutionPolicy(
        max_steps=DEFAULT_MAX_STEPS,
        max_tool_calls=DEFAULT_MAX_TOOL_CALLS,
        max_tool_calls_per_step=DEFAULT_MAX_TOOL_CALLS_PER_STEP,
        max_wall_time_seconds=DEFAULT_MAX_WALL_TIME_SECONDS,
        model_timeout_seconds=settings.deepseek_timeout_seconds,
        tool_timeout_seconds=settings.tool_timeout_seconds,
        max_context_messages=DEFAULT_MAX_CONTEXT_MESSAGES,
        max_context_chars=DEFAULT_MAX_CONTEXT_CHARS,
        max_tool_result_chars=settings.tool_result_max_chars,
        max_event_queue_size=DEFAULT_MAX_EVENT_QUEUE_SIZE,
        allowed_tools=basic_tool_names(registry),
    )


def parse_agent_request(payload: object) -> AgentRequest:
    try:
        return AgentRequest.model_validate(payload)
    except ValidationError:
        raise PolicyError("AGENT_POLICY_INVALID", "请求参数无效") from None


def merge_execution_policy(
    server: ExecutionPolicy,
    request: AgentRequest | None = None,
) -> ExecutionPolicy:
    """有效值取服务端上限与请求值中的更严者。省略字段保持服务端值。"""
    if request is None:
        return ExecutionPolicy.model_validate(server.model_dump())
    requested = request.policy or RequestedPolicy()
    updates: dict[str, object] = {}
    for name in _LIMIT_FIELDS:
        value = getattr(requested, name)
        if value is None:
            continue
        if value > getattr(server, name):
            raise PolicyError("AGENT_POLICY_INVALID", f"{name} 超出服务端上限")
        updates[name] = value
    updates["allowed_tools"] = _merge_tools(server.allowed_tools, request.allowed_tools)
    data = server.model_dump()
    data.update(updates)
    try:
        return ExecutionPolicy.model_validate(data)
    except ValidationError:
        raise PolicyError("AGENT_POLICY_INVALID", "请求策略无效") from None


def _merge_tools(server_tools: tuple[str, ...], requested: list[str] | None) -> tuple[str, ...]:
    if requested is None:
        return server_tools
    if len(requested) != len(set(requested)):
        raise PolicyError("AGENT_POLICY_INVALID", "allowed_tools 包含重复名称")
    allowed = set(server_tools)
    for name in requested:
        if not _TOOL_NAME.fullmatch(name):
            raise PolicyError("AGENT_POLICY_INVALID", "工具名称不合法")
        if name not in allowed:
            raise PolicyError("AGENT_TOOL_NOT_ALLOWED", "请求的工具不在服务端允许范围内")
    return tuple(sorted(requested))
