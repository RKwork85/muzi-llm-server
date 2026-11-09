"""Strict, versioned contracts for the Interactive Planning System.

These models describe wire and domain data only. They intentionally do not
perform persistence, authorization, tool execution, or state transitions.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROTOCOL_VERSION = "1.0"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_CLIENT_SUBMISSION_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


class StrictContract(BaseModel):
    # FastAPI validates already-decoded JSON values, so keep normal JSON scalar
    # coercion (notably ISO timestamps) while still rejecting unknown fields.
    model_config = ConfigDict(extra="forbid")


class InteractionType(StrEnum):
    """组件类型白名单。LLM 只能从这些类型中选择，服务端据此校验并分发渲染。

    命名统一为 snake_case（single_select/multi_select 为历史遗留的权威名，
    文档中的 single_choice/multi_choice 与此等价）。
    """

    SINGLE_SELECT = "single_select"
    MULTI_SELECT = "multi_select"
    TEXT_INPUT = "text_input"
    CONFIRMATION = "confirmation"
    PLAN_REVIEW = "plan_review"
    PROGRESS = "progress"
    ERROR = "error"
    # 以下为 M3/M4 计划编辑能力预留的组件类型（M2 仅登记白名单，暂不渲染交互）
    ASSUMPTION_EDITOR = "assumption_editor"
    PLAN_EDITOR = "plan_editor"
    STEP_REORDER = "step_reorder"
    PLAN_DIFF = "plan_diff"


class InteractionStatus(StrEnum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"


class ActionType(StrEnum):
    """统一交互动作白名单。每个动作都必须有对应的 target_type 组合。"""

    # 输入类（对应现有卡片提交/跳过）
    SUBMIT = "submit"
    SKIP = "skip"
    # 背景假设类（M3）
    ASSUMPTION_CONFIRM = "assumption_confirm"
    ASSUMPTION_EDIT = "assumption_edit"
    ASSUMPTION_REJECT = "assumption_reject"
    # 目标与约束类（M4）
    GOAL_EDIT = "goal_edit"
    CONSTRAINT_ADD = "constraint_add"
    CONSTRAINT_EDIT = "constraint_edit"
    CONSTRAINT_REMOVE = "constraint_remove"
    # 步骤类（M4）
    STEP_ADD = "step_add"
    STEP_EDIT = "step_edit"
    STEP_REMOVE = "step_remove"
    STEP_SPLIT = "step_split"
    STEP_MERGE = "step_merge"
    STEP_REORDER = "step_reorder"
    # 版本类（M6）
    REVISION_PREVIEW = "revision_preview"
    REVISION_CONFIRM = "revision_confirm"
    REVISION_ROLLBACK = "revision_rollback"


class TargetType(StrEnum):
    """动作所作用的目标对象类型白名单。"""

    INTERACTION = "interaction"
    GOAL = "goal"
    ASSUMPTION = "assumption"
    CONSTRAINT = "constraint"
    STEP = "step"
    DECISION = "decision"
    REVISION = "revision"


# 动作 → 合法目标对象类型的映射。服务端据此拒绝不匹配的 (action, target) 组合。
ACTION_TARGETS: dict[ActionType, frozenset[TargetType]] = {
    ActionType.SUBMIT: frozenset({TargetType.INTERACTION}),
    ActionType.SKIP: frozenset({TargetType.INTERACTION}),
    ActionType.ASSUMPTION_CONFIRM: frozenset({TargetType.ASSUMPTION}),
    ActionType.ASSUMPTION_EDIT: frozenset({TargetType.ASSUMPTION}),
    ActionType.ASSUMPTION_REJECT: frozenset({TargetType.ASSUMPTION}),
    ActionType.GOAL_EDIT: frozenset({TargetType.GOAL}),
    ActionType.CONSTRAINT_ADD: frozenset({TargetType.CONSTRAINT}),
    ActionType.CONSTRAINT_EDIT: frozenset({TargetType.CONSTRAINT}),
    ActionType.CONSTRAINT_REMOVE: frozenset({TargetType.CONSTRAINT}),
    ActionType.STEP_ADD: frozenset({TargetType.STEP}),
    ActionType.STEP_EDIT: frozenset({TargetType.STEP}),
    ActionType.STEP_REMOVE: frozenset({TargetType.STEP}),
    ActionType.STEP_SPLIT: frozenset({TargetType.STEP}),
    ActionType.STEP_MERGE: frozenset({TargetType.STEP}),
    ActionType.STEP_REORDER: frozenset({TargetType.STEP}),
    ActionType.REVISION_PREVIEW: frozenset({TargetType.REVISION}),
    ActionType.REVISION_CONFIRM: frozenset({TargetType.REVISION}),
    ActionType.REVISION_ROLLBACK: frozenset({TargetType.REVISION}),
}


class PlanStatus(StrEnum):
    UNDERSTANDING = "understanding"
    CLARIFYING = "clarifying"
    DRAFTING = "drafting"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    READY_FOR_HANDOFF = "ready_for_handoff"
    EXECUTING = "executing"
    COMPLETED = "completed"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class PlanningEventType(StrEnum):
    CHAT_RUN_STARTED = "chat.run.started"
    CHAT_DELTA = "chat.delta"
    CHAT_TOOL = "chat.tool"
    CHAT_STATUS = "chat.status"
    CHAT_DONE = "chat.done"
    CHAT_ERROR = "chat.error"
    CHAT_CANCELLED = "chat.cancelled"
    PLAN_CREATED = "plan.created"
    PLAN_UPDATED = "plan.updated"
    PLAN_APPROVED = "plan.approved"
    PLAN_PAUSED = "plan.paused"
    PLAN_RESUMED = "plan.resumed"
    PLAN_CANCELLED = "plan.cancelled"
    PLAN_COMPLETED = "plan.completed"
    INTERACTION_READY = "interaction.ready"
    INTERACTION_REQUIRED = "interaction.required"
    INTERACTION_SUBMITTED = "interaction.submitted"
    INTERACTION_SKIPPED = "interaction.skipped"
    MODE_CHANGED = "conversation.mode_changed"
    HANDOFF_CREATED = "handoff.created"
    RUN_STARTED = "run.started"
    RUN_PROGRESS = "run.progress"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"


class InteractionOption(StrictContract):
    id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    recommended: bool = False

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("option id contains unsupported characters")
        return value


class InteractionPlanStep(StrictContract):
    id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=1000)
    # M4：步骤间顺序依赖（引用其他 step 的 id），供前端就地重排提示与后端校验。
    depends_on: list[str] = Field(default_factory=list, max_length=50)


class InteractionPlanReview(StrictContract):
    goal: str = Field(min_length=1, max_length=4000)
    assumptions: list[str] = Field(default_factory=list, max_length=50)
    # M3：携带结构化假设供前端就地确认/编辑/否定。空列表时前端退化为只读展示。
    assumption_items: list[Assumption] = Field(default_factory=list, max_length=100)
    decisions: dict[str, Any] = Field(default_factory=dict)
    decision_sources: dict[str, str] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list, max_length=50)
    steps: list[InteractionPlanStep] = Field(min_length=1, max_length=100)


class Interaction(StrictContract):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "allOf": [
                {
                    "if": {"properties": {"type": {"enum": ["single_select", "multi_select"]}}},
                    "then": {"properties": {"options": {"minItems": 1}}},
                },
                {
                    "if": {"properties": {"type": {"enum": ["text_input", "confirmation", "plan_review", "progress", "error"]}}},
                    "then": {"properties": {"options": {"maxItems": 0}}},
                },
            ],
        },
    )
    protocol_version: Literal["1.0"] = PROTOCOL_VERSION
    interaction_id: str = Field(min_length=1, max_length=128)
    plan_id: str = Field(min_length=1, max_length=128)
    plan_revision: int = Field(ge=0)
    type: InteractionType
    prompt: str = Field(min_length=1, max_length=2000)
    description: str | None = Field(default=None, max_length=4000)
    status: InteractionStatus
    options: list[InteractionOption] = Field(default_factory=list, max_length=20)
    plan_review: InteractionPlanReview | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None

    @field_validator("interaction_id", "plan_id")
    @classmethod
    def valid_identifiers(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("identifier contains unsupported characters")
        return value

    @field_validator("expires_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("expires_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_options(self) -> Interaction:
        option_ids = [option.id for option in self.options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("option ids must be unique")
        if self.type in {InteractionType.SINGLE_SELECT, InteractionType.MULTI_SELECT} and not self.options:
            raise ValueError("selection interactions require options")
        if self.type not in {InteractionType.SINGLE_SELECT, InteractionType.MULTI_SELECT} and self.options:
            raise ValueError("only selection interactions may contain options")
        if self.type == InteractionType.PLAN_REVIEW and self.plan_review is None:
            raise ValueError("plan review interactions require a plan snapshot")
        if self.type != InteractionType.PLAN_REVIEW and self.plan_review is not None:
            raise ValueError("plan snapshot is only valid for plan review interactions")
        return self


class InteractionSubmission(StrictContract):
    interaction_id: str = Field(min_length=1, max_length=128)
    expected_plan_revision: int = Field(ge=0)
    client_submission_id: str = Field(min_length=8, max_length=128)
    value: Any

    @field_validator("interaction_id")
    @classmethod
    def valid_interaction_id(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("interaction id contains unsupported characters")
        return value

    @field_validator("client_submission_id")
    @classmethod
    def valid_client_submission_id(cls, value: str) -> str:
        if not _CLIENT_SUBMISSION_ID.fullmatch(value):
            raise ValueError("client submission id is invalid")
        return value


class InteractionAction(StrictContract):
    """统一交互动作协议。

    所有用户对计划的修改（提交选择、跳过、编辑假设、增删步骤、排序、版本操作等）
    都通过它提交，由服务端校验归属、版本一致性、动作合法性后分发执行。

    与旧 InteractionSubmission 的关系：InteractionSubmission 保留以兼容历史卡片，
    其语义等价于 (component_type=对应组件, action_type=submit, target_type=interaction)。
    """

    plan_id: str = Field(min_length=1, max_length=128)
    plan_revision: int = Field(ge=0)
    component_type: InteractionType
    action_type: ActionType
    target_type: TargetType
    target_id: str | None = Field(default=None, max_length=128)
    interaction_id: str | None = Field(default=None, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=8, max_length=128)

    @field_validator("plan_id", "target_id", "interaction_id")
    @classmethod
    def valid_identifiers(cls, value: str | None) -> str | None:
        if value is not None and not _IDENTIFIER.fullmatch(value):
            raise ValueError("identifier contains unsupported characters")
        return value

    @field_validator("idempotency_key")
    @classmethod
    def valid_idempotency_key(cls, value: str) -> str:
        if not _CLIENT_SUBMISSION_ID.fullmatch(value):
            raise ValueError("idempotency key is invalid")
        return value

    @model_validator(mode="after")
    def action_targets_are_compatible(self) -> InteractionAction:
        allowed = ACTION_TARGETS.get(self.action_type)
        if allowed is None or self.target_type not in allowed:
            raise ValueError(f"action {self.action_type.value} cannot target {self.target_type.value}")
        # 卡片级动作必须携带 interaction_id 定位目标卡片。
        if self.target_type == TargetType.INTERACTION and not self.interaction_id:
            raise ValueError("interaction-targeting action requires interaction_id")
        return self


class AssumptionStatus(StrEnum):
    """假设的生命周期：提议 → 确认 / 否定。"""

    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class Assumption(StrictContract):
    """结构化背景假设。id 由服务端生成并在文本不变时保持稳定。

    depends_on 存其他 assumption 的 id（链式假设）；decision/step 的
    depends_on 也可引用 assumption id，供 M3 影响分析定位受影响的决策与步骤。
    """

    id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=2000)
    depends_on: list[str] = Field(default_factory=list, max_length=50)
    status: AssumptionStatus = AssumptionStatus.PROPOSED

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("assumption id contains unsupported characters")
        return value

    @field_validator("depends_on")
    @classmethod
    def valid_dependencies(cls, value: list[str]) -> list[str]:
        if any(not _IDENTIFIER.fullmatch(item) for item in value):
            raise ValueError("assumption dependency contains unsupported characters")
        return value


class PlanStep(StrictContract):
    id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    depends_on: list[str] = Field(default_factory=list, max_length=50)


class PlanDecision(StrictContract):
    key: str = Field(min_length=1, max_length=128)
    value: Any
    source: Literal["user", "assumed", "system"]
    confidence: float | None = Field(default=None, ge=0, le=1)
    depends_on: list[str] = Field(default_factory=list, max_length=50)


class PlanSnapshot(StrictContract):
    protocol_version: Literal["1.0"] = PROTOCOL_VERSION
    conversation_id: str = Field(min_length=1, max_length=128)
    plan_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=0)
    status: PlanStatus
    goal: str = Field(min_length=1, max_length=4000)
    assumptions: list[str] = Field(default_factory=list, max_length=100)
    # M3：结构化假设（带 id/status/depends_on），assumptions 作为纯文本展示层保留。
    assumption_items: list[Assumption] = Field(default_factory=list, max_length=100)
    decisions: list[PlanDecision] = Field(default_factory=list, max_length=100)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    steps: list[PlanStep] = Field(default_factory=list, max_length=100)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def timestamps_are_ordered(self) -> PlanSnapshot:
        if self.created_at.utcoffset() is None or self.updated_at.utcoffset() is None:
            raise ValueError("plan timestamps must include a timezone")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        return self


class PlanningEvent(StrictContract):
    protocol_version: Literal["1.0"] = PROTOCOL_VERSION
    event_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1)
    conversation_id: str = Field(min_length=1, max_length=128)
    plan_id: str | None = Field(default=None, max_length=128)
    run_id: str | None = Field(default=None, max_length=128)
    type: PlanningEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime

    @field_validator("event_id", "conversation_id", "plan_id", "run_id")
    @classmethod
    def valid_optional_identifiers(cls, value: str | None) -> str | None:
        if value is not None and not _IDENTIFIER.fullmatch(value):
            raise ValueError("identifier contains unsupported characters")
        return value

    @field_validator("occurred_at")
    @classmethod
    def event_time_has_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value


class PlanApprovalRequest(StrictContract):
    expected_revision: int = Field(ge=0)


class ModeSwitchRequest(StrictContract):
    expected_revision: int = Field(ge=0)
    target_mode: Literal["ask", "plan", "agent"]


class ExecutionAuthorizationRequest(StrictContract):
    approved_plan_id: str = Field(min_length=1, max_length=128)
    approved_revision: int = Field(ge=0)
    expected_revision: int = Field(ge=0)
    authorization_id: str = Field(min_length=8, max_length=128)


class HandoffStep(StrictContract):
    id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)


class PlanHandoff(StrictContract):
    protocol_version: Literal["1.0"] = PROTOCOL_VERSION
    conversation_id: str = Field(min_length=1, max_length=128)
    approved_plan_id: str = Field(min_length=1, max_length=128)
    approved_revision: int = Field(ge=0)
    goal: str = Field(min_length=1, max_length=4000)
    confirmed_decisions: dict[str, Any] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    steps: list[HandoffStep] = Field(min_length=1, max_length=100)
    authorization: Literal["revalidate_at_execution"]


PLAN_TRANSITIONS: dict[PlanStatus, frozenset[PlanStatus]] = {
    PlanStatus.UNDERSTANDING: frozenset({PlanStatus.CLARIFYING, PlanStatus.DRAFTING, PlanStatus.PAUSED, PlanStatus.CANCELLED, PlanStatus.FAILED}),
    PlanStatus.CLARIFYING: frozenset({PlanStatus.DRAFTING, PlanStatus.PAUSED, PlanStatus.CANCELLED, PlanStatus.FAILED, PlanStatus.SUPERSEDED}),
    PlanStatus.DRAFTING: frozenset({PlanStatus.CLARIFYING, PlanStatus.AWAITING_APPROVAL, PlanStatus.PAUSED, PlanStatus.CANCELLED, PlanStatus.FAILED, PlanStatus.SUPERSEDED}),
    PlanStatus.AWAITING_APPROVAL: frozenset({PlanStatus.DRAFTING, PlanStatus.APPROVED, PlanStatus.PAUSED, PlanStatus.CANCELLED, PlanStatus.SUPERSEDED}),
    PlanStatus.APPROVED: frozenset({PlanStatus.DRAFTING, PlanStatus.READY_FOR_HANDOFF, PlanStatus.PAUSED, PlanStatus.CANCELLED, PlanStatus.SUPERSEDED}),
    PlanStatus.READY_FOR_HANDOFF: frozenset({PlanStatus.DRAFTING, PlanStatus.EXECUTING, PlanStatus.PAUSED, PlanStatus.CANCELLED, PlanStatus.SUPERSEDED}),
    PlanStatus.EXECUTING: frozenset({PlanStatus.COMPLETED, PlanStatus.PAUSED, PlanStatus.CANCELLED, PlanStatus.FAILED}),
    PlanStatus.PAUSED: frozenset({PlanStatus.UNDERSTANDING, PlanStatus.CLARIFYING, PlanStatus.DRAFTING, PlanStatus.AWAITING_APPROVAL, PlanStatus.APPROVED, PlanStatus.READY_FOR_HANDOFF, PlanStatus.EXECUTING, PlanStatus.CANCELLED, PlanStatus.FAILED, PlanStatus.SUPERSEDED}),
    PlanStatus.COMPLETED: frozenset(),
    PlanStatus.CANCELLED: frozenset(),
    PlanStatus.FAILED: frozenset(),
    PlanStatus.SUPERSEDED: frozenset(),
}


def can_transition_plan(current: PlanStatus, target: PlanStatus) -> bool:
    """Return whether a plan state transition is allowed by protocol v1."""
    return target in PLAN_TRANSITIONS[current]
