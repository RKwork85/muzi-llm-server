"""Agent 领域契约。这里只定义数据和状态，不执行循环，也不暴露 HTTP。"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TaskStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LIMIT_REACHED = "limit_reached"


TERMINAL_TASK_STATUSES = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.LIMIT_REACHED,
    }
)


class ToolExecutionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    REJECTED = "rejected"


class AgentEventType(str, Enum):
    STARTED = "agent.started"
    STEP_STARTED = "agent.step.started"
    MODEL_STARTED = "agent.model.started"
    MODEL_COMPLETED = "agent.model.completed"
    TOOL_STARTED = "agent.tool.started"
    TOOL_COMPLETED = "agent.tool.completed"
    TOOL_FAILED = "agent.tool.failed"
    COMPLETED = "agent.completed"
    FAILED = "agent.failed"
    CANCELLED = "agent.cancelled"
    LIMIT_REACHED = "agent.limit_reached"


class UsageSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class RequestedPolicy(BaseModel):
    """调用方可选的收紧项。未提供的字段沿用服务端策略。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    max_steps: int | None = Field(default=None, ge=1)
    max_tool_calls: int | None = Field(default=None, ge=1)
    max_tool_calls_per_step: int | None = Field(default=None, ge=1)
    max_wall_time_seconds: float | None = Field(default=None, gt=0)
    model_timeout_seconds: float | None = Field(default=None, gt=0)
    tool_timeout_seconds: float | None = Field(default=None, gt=0)
    max_context_messages: int | None = Field(default=None, ge=1)
    max_context_chars: int | None = Field(default=None, ge=1)
    max_tool_result_chars: int | None = Field(default=None, ge=96)
    max_event_queue_size: int | None = Field(default=None, ge=1)


_TOOLSET_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class AgentRequest(BaseModel):
    """一次 Agent 运行的受控请求。不能携带工具定义、handler 或系统消息。"""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=32000)
    prompt_name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    allowed_tools: list[str] | None = Field(default=None, max_length=16)
    toolsets: list[str] | None = Field(default=None, max_length=4)
    grants: list[Literal["write_file", "delete_file", "run_command"]] | None = Field(
        default=None,
        max_length=3,
    )
    policy: RequestedPolicy | None = None

    @field_validator("grants")
    @classmethod
    def _check_grants(
        cls, value: list[Literal["write_file", "delete_file", "run_command"]] | None
    ) -> list[str] | None:
        if value is None:
            return None
        if len(value) != len(set(value)):
            raise ValueError("grants 重复")
        return list(value)

    @field_validator("toolsets")
    @classmethod
    def _check_toolsets(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if not value or len(value) != len(set(value)):
            raise ValueError("toolsets 无效")
        if any(_TOOLSET_NAME.fullmatch(item) is None for item in value):
            raise ValueError("toolset 名称不合法")
        return value


class AgentRunState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    status: TaskStatus
    step: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    error_code: str | None = None


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    status: TaskStatus
    answer: str | None = None
    steps: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    error_code: str | None = None
    usage: UsageSnapshot | None = None
    artifacts: list["Artifact"] | None = None


class Artifact(BaseModel):
    """一次运行里的图表产物。事件和模型消息不携带正文。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^art_[1-9][0-9]{0,3}$")
    type: Literal["mermaid", "file", "package"]
    title: str | None = Field(default=None, max_length=80)
    content: str = Field(min_length=1, max_length=20000)
    content_type: Literal["text/vnd.mermaid", "application/vnd.qs.file+json"] = "text/vnd.mermaid"
    source_tool: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class ToolExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_call_id: str = Field(max_length=128)
    tool_name: str = Field(max_length=64)
    status: ToolExecutionStatus
    content: str
    error_code: str | None = None
    duration_ms: int = Field(ge=0)


class EventEnvelope(BaseModel):
    """事件形状。本里程碑不发布事件，载荷过滤留到后续里程碑。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1)
    timestamp: str = Field(min_length=1, max_length=40)
    type: AgentEventType
    data: dict[str, Any] = Field(default_factory=dict)


class RegisteredToolView(BaseModel):
    """注册表条目的只读视图。权限仍以 ToolRegistry 为准。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str
    risk_level: str
    enabled: bool
