"""Planning decision validation and deterministic in-process orchestration.

M2 keeps the orchestration independent from HTTP and SQL. M4 replaces the
temporary store with a transactional repository without changing these rules.
"""

from __future__ import annotations

import json
import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.core.context import get_request_id
from app.planning.contracts import (
    ActionType,
    FormField,
    Interaction,
    InteractionAction,
    InteractionOption,
    InteractionStatus,
    InteractionSubmission,
    InteractionType,
    PlanDecision,
    PlanStep,
    PlanSnapshot,
    PlanStatus,
    PlanningEvent,
    PlanningEventType,
    TargetType,
    can_transition_plan,
)
from app.planning import revisions
from app.providers.base import CompletionResult, LLMProvider, ProviderChatRequest
from app.tools.capabilities import PLAN_TOOLS
from app.tools.registry import ToolRegistry

logger = logging.getLogger("muzi_llm")

# 走确定性修订路径（产生新版本）的动作类型。
_PLAN_REVISION_ACTIONS = frozenset(
    {
        ActionType.ASSUMPTION_CONFIRM,
        ActionType.ASSUMPTION_EDIT,
        ActionType.ASSUMPTION_REJECT,
        ActionType.GOAL_EDIT,
        ActionType.CONSTRAINT_ADD,
        ActionType.CONSTRAINT_REMOVE,
        ActionType.STEP_ADD,
        ActionType.STEP_EDIT,
        ActionType.STEP_REMOVE,
        ActionType.STEP_SPLIT,
        ActionType.STEP_MERGE,
        ActionType.STEP_REORDER,
    }
)


class PlanningConflict(ValueError):
    """Raised when an interaction is stale or a user answer conflicts."""


class PlanningNotFound(LookupError):
    """Raised when a plan, interaction, or idempotency key is not available."""


class PlanningPolicyError(ValueError):
    """Raised when a model proposal violates server-owned planning policy."""


class DecisionAction(StrEnum):
    RESPOND = "respond"
    ASK_USER = "ask_user"
    UPDATE_PLAN = "update_plan"
    REQUEST_APPROVAL = "request_approval"
    READY_FOR_HANDOFF = "ready_for_handoff"


class ProposedInteraction(BaseModel):
    """Model-authored interaction content; identifiers and lifecycle belong to the server."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["single_select", "multi_select", "text_input", "text_area", "form", "confirmation"]
    prompt: str = Field(min_length=1, max_length=2000)
    description: str | None = Field(default=None, max_length=4000)
    options: list[InteractionOption] = Field(default_factory=list, max_length=20)
    form: list[FormField] | None = Field(default=None, max_length=20)
    constraints: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_shape(self) -> ProposedInteraction:
        selection = self.type in {"single_select", "multi_select"}
        if selection and not self.options:
            raise ValueError("selection interactions require options")
        if not selection and self.options:
            raise ValueError("only selection interactions may contain options")
        if len({option.id for option in self.options}) != len(self.options):
            raise ValueError("option ids must be unique")
        if self.type == "form":
            if not self.form:
                raise ValueError("form interactions require form fields")
            if len({field.key for field in self.form}) != len(self.form):
                raise ValueError("form field keys must be unique")
        elif self.form:
            raise ValueError("form fields are only valid for form interactions")
        return self


class ProposedDecision(PlanDecision):
    source: Literal["assumed", "system"]


class DecisionProposal(BaseModel):
    """Untrusted model proposal. Validation does not itself apply an action."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: str = Field(pattern=r"^1\.0$")
    action: DecisionAction
    response: str = Field(default="", max_length=8000)
    goal: str | None = Field(default=None, max_length=4000)
    assumptions: list[str] = Field(default_factory=list, max_length=100)
    proposed_decisions: list[ProposedDecision] = Field(default_factory=list, max_length=100)
    steps: list[PlanStep] = Field(default_factory=list, max_length=100)
    interaction: ProposedInteraction | None = None

    @model_validator(mode="after")
    def action_has_required_data(self) -> DecisionProposal:
        if self.action == DecisionAction.ASK_USER:
            if self.interaction is None:
                raise ValueError("ask_user requires one interaction")
        elif self.interaction is not None:
            raise ValueError("only ask_user may propose an interaction")
        if self.action in {DecisionAction.UPDATE_PLAN, DecisionAction.REQUEST_APPROVAL} and not self.goal:
            raise ValueError("plan actions require a goal")
        if self.action == DecisionAction.REQUEST_APPROVAL and not self.steps:
            raise ValueError("approval requires plan steps")
        if self.action == DecisionAction.RESPOND and not self.response.strip():
            raise ValueError("respond requires visible response text")
        return self


def planning_transition_path(current: PlanStatus, target: PlanStatus) -> tuple[PlanStatus, ...]:
    """Model revisions may revisit drafts, but cannot skip approval or execution gates."""
    if current == target and target in {PlanStatus.CLARIFYING, PlanStatus.DRAFTING, PlanStatus.AWAITING_APPROVAL}:
        return (target,)
    if can_transition_plan(current, target):
        return (target,)
    if can_transition_plan(current, PlanStatus.DRAFTING) and can_transition_plan(PlanStatus.DRAFTING, target):
        return (PlanStatus.DRAFTING, target)
    raise PlanningConflict(f"invalid plan transition: {current.value} -> {target.value}")


@dataclass
class _PlanRecord:
    owner_id: str
    current: PlanSnapshot
    revisions: list[PlanSnapshot] = field(default_factory=list)
    interactions: dict[str, Interaction] = field(default_factory=dict)
    submissions: dict[str, dict[str, Any]] = field(default_factory=dict)
    actions: dict[str, dict[str, Any]] = field(default_factory=dict)
    events: list[PlanningEvent] = field(default_factory=list)


def _payload_step_data(raw: Any) -> PlanStep:
    if not isinstance(raw, dict):
        raise PlanningPolicyError("step action requires a step object")
    try:
        return PlanStep.model_validate(raw)
    except ValidationError as exc:
        raise PlanningPolicyError("step action contains an invalid step") from None


def compute_revision_changes(plan: PlanSnapshot, action: InteractionAction) -> tuple[dict[str, Any], dict[str, Any]]:
    """把修订动作转换为字段变更与影响分析结果（供内存版与持久化版共用）。"""
    changes: dict[str, Any]
    affected: dict[str, Any] = {}

    if action.action_type == ActionType.ASSUMPTION_CONFIRM:
        changes, affected = revisions.confirm_assumption(plan, action.target_id or "")
    elif action.action_type == ActionType.ASSUMPTION_EDIT:
        changes, affected = revisions.edit_assumption(plan, action.target_id or "", str(action.payload.get("text", "")).strip())
    elif action.action_type == ActionType.ASSUMPTION_REJECT:
        changes, affected = revisions.reject_assumption(plan, action.target_id or "", action.payload.get("replacement"))
    elif action.action_type == ActionType.GOAL_EDIT:
        goal = str(action.payload.get("goal", "")).strip()
        if not goal:
            raise PlanningPolicyError("goal_edit requires a non-empty goal")
        changes = {"goal": goal}
    elif action.action_type == ActionType.CONSTRAINT_ADD:
        constraint = str(action.payload.get("constraint", "")).strip()
        if not constraint:
            raise PlanningPolicyError("constraint_add requires a non-empty constraint")
        changes = {"constraints": [*plan.constraints, constraint]}
    elif action.action_type == ActionType.CONSTRAINT_REMOVE:
        target = action.target_id or ""
        changes = {"constraints": [item for item in plan.constraints if item != target]}
    elif action.action_type == ActionType.STEP_ADD:
        changes, affected = revisions.add_step(plan, _payload_step_data(action.payload.get("step")))
    elif action.action_type == ActionType.STEP_EDIT:
        changes, affected = revisions.edit_step(plan, _payload_step_data(action.payload.get("step")))
    elif action.action_type == ActionType.STEP_REMOVE:
        changes, affected = revisions.remove_step(plan, action.target_id or "")
    elif action.action_type == ActionType.STEP_SPLIT:
        children = [_payload_step_data(item) for item in (action.payload.get("children") or [])]
        changes, affected = revisions.split_step(plan, action.target_id or "", children)
    elif action.action_type == ActionType.STEP_MERGE:
        step_ids = [str(item) for item in (action.payload.get("step_ids") or [])]
        merged = _payload_step_data(action.payload.get("merged"))
        changes, affected = revisions.merge_steps(plan, step_ids, merged)
    elif action.action_type == ActionType.STEP_REORDER:
        ordered_ids = [str(item) for item in (action.payload.get("ordered_ids") or [])]
        changes, affected = revisions.reorder_steps(plan, ordered_ids)
    else:
        raise PlanningPolicyError(f"action not implemented in this milestone: {action.action_type.value}")

    return changes, affected


class PlanningOrchestrator:
    """Apply validated planning decisions and user submissions deterministically."""

    def __init__(self) -> None:
        self._plans: dict[str, _PlanRecord] = {}

    def create_plan(self, *, owner_id: str, conversation_id: str, goal: str) -> PlanSnapshot:
        if not owner_id:
            raise PlanningPolicyError("authenticated owner is required")
        now = datetime.now(UTC)
        plan_id = f"plan_{uuid4().hex}"
        plan = PlanSnapshot(
            conversation_id=conversation_id,
            plan_id=plan_id,
            revision=0,
            status=PlanStatus.UNDERSTANDING,
            goal=goal,
            created_at=now,
            updated_at=now,
        )
        record = _PlanRecord(owner_id=owner_id, current=plan, revisions=[plan])
        self._plans[plan_id] = record
        self._event(record, PlanningEventType.PLAN_CREATED, {"revision": plan.revision})
        return plan

    def get_plan(self, plan_id: str, *, owner_id: str) -> PlanSnapshot:
        return self._owned(plan_id, owner_id).current

    def apply_proposal(
        self,
        plan_id: str,
        proposal: DecisionProposal,
        *,
        owner_id: str,
        expected_revision: int,
    ) -> PlanSnapshot:
        record = self._owned(plan_id, owner_id)
        plan = record.current
        self._expect_revision(plan, expected_revision)
        if proposal.action == DecisionAction.READY_FOR_HANDOFF:
            raise PlanningPolicyError("handoff requires separate approval, mode switch, and authorization")
        if proposal.action == DecisionAction.RESPOND:
            return plan
        if proposal.action in {DecisionAction.RESPOND, DecisionAction.ASK_USER}:
            target = PlanStatus.CLARIFYING if proposal.action == DecisionAction.ASK_USER else plan.status
            for intermediate in planning_transition_path(plan.status, target)[:-1]:
                plan = self._revise(plan, status=intermediate)
                record.current = plan
                record.revisions.append(plan)
                self._event(record, PlanningEventType.PLAN_UPDATED, {"revision": plan.revision, "status": intermediate.value})
            updated = self._revise(plan, status=target)
            record.current = updated
            record.revisions.append(updated)
            if proposal.interaction is not None:
                interaction = Interaction.model_validate(
                    {
                        **proposal.interaction.model_dump(),
                        "protocol_version": "1.0",
                        "interaction_id": f"int_{uuid4().hex}",
                        "plan_id": plan_id,
                        "plan_revision": updated.revision,
                        "status": InteractionStatus.PENDING,
                    }
                )
                record.interactions[interaction.interaction_id] = interaction
                self._event(
                    record,
                    PlanningEventType.INTERACTION_REQUIRED,
                    {"interaction_id": interaction.interaction_id, "plan_revision": updated.revision},
                )
            else:
                self._event(record, PlanningEventType.PLAN_UPDATED, {"revision": updated.revision})
            return updated

        if proposal.goal:
            target = PlanStatus.DRAFTING
            if proposal.action == DecisionAction.REQUEST_APPROVAL:
                target = PlanStatus.AWAITING_APPROVAL
            transition_path = planning_transition_path(plan.status, target)
            decisions = list(plan.decisions)
            for proposed in proposal.proposed_decisions:
                if proposed.source == "user":
                    raise PlanningPolicyError("model cannot mark a decision as user-confirmed")
                if any(item.key == proposed.key and item.source == "user" for item in decisions):
                    continue
                decisions = [item for item in decisions if item.key != proposed.key]
                decisions.append(proposed)
            steps = list(plan.steps)
            if proposal.steps:
                try:
                    steps = [PlanStep.model_validate(item) for item in proposal.steps]
                except ValidationError:
                    raise PlanningPolicyError("planning proposal contains invalid steps") from None
            for intermediate in transition_path[:-1]:
                plan = self._revise(plan, status=intermediate)
                record.current = plan
                record.revisions.append(plan)
                self._event(record, PlanningEventType.PLAN_UPDATED, {"revision": plan.revision, "status": intermediate.value})
            assumption_texts = proposal.assumptions or plan.assumptions
            updated = self._revise(
                plan,
                status=target,
                goal=proposal.goal,
                assumptions=assumption_texts,
                assumption_items=revisions.sync_assumptions(plan, assumption_texts),
                decisions=decisions,
                steps=steps,
            )
            record.revisions.append(updated)
            record.current = updated
            event_type = PlanningEventType.PLAN_UPDATED
            self._event(record, event_type, {"revision": updated.revision, "status": updated.status.value})
            return updated
        raise PlanningPolicyError("proposal cannot be applied to the current plan")

    def submit_interaction(
        self,
        plan_id: str,
        submission: InteractionSubmission,
        *,
        owner_id: str,
    ) -> PlanSnapshot:
        record = self._owned(plan_id, owner_id)
        prior = record.submissions.get(submission.client_submission_id)
        if prior is not None:
            if prior["interaction_id"] != submission.interaction_id or prior["value"] != submission.value:
                raise PlanningConflict("idempotency key was reused with a different submission")
            return record.current

        plan = record.current
        self._expect_revision(plan, submission.expected_plan_revision)
        interaction = record.interactions.get(submission.interaction_id)
        if interaction is None or interaction.plan_id != plan_id:
            raise PlanningNotFound("pending interaction not found")
        if interaction.status != InteractionStatus.PENDING:
            raise PlanningConflict("interaction is no longer pending")
        if interaction.plan_revision != plan.revision:
            raise PlanningConflict("interaction belongs to a stale plan revision")
        value = self._validate_submission_value(interaction, submission.value)
        decisions = list(plan.decisions)
        key = self._decision_key(interaction)
        old = next((item for item in decisions if item.key == key), None)
        if old is not None and old.source == "user" and old.value != value:
            # Editing a prior answer creates a new revision while retaining an audit trail.
            decisions = [item for item in decisions if item.key != key]
        decisions.append(PlanDecision(key=key, value=value, source="user", confidence=1.0))
        updated = self._revise(
            plan,
            status=PlanStatus.DRAFTING,
            decisions=decisions,
        )
        record.current = updated
        record.revisions.append(updated)
        record.interactions[interaction.interaction_id] = interaction.model_copy(
            update={"status": InteractionStatus.SUBMITTED}
        )
        record.submissions[submission.client_submission_id] = {
            "interaction_id": submission.interaction_id,
            "value": value,
        }
        self._event(
            record,
            PlanningEventType.INTERACTION_SUBMITTED,
            {"interaction_id": interaction.interaction_id, "revision": updated.revision},
        )
        self._event(record, PlanningEventType.PLAN_UPDATED, {"revision": updated.revision})
        return updated

    def apply_action(
        self,
        plan_id: str,
        action: InteractionAction,
        *,
        owner_id: str,
    ) -> tuple[PlanSnapshot, dict[str, Any]]:
        """统一交互动作入口，返回 (计划快照, 影响分析结果)。

        校验链：归属 → 幂等 → 版本 CAS → 动作白名单（契约层已校验 action-target 兼容）→
        目标定位 → 分发执行。
        """
        record = self._owned(plan_id, owner_id)

        prior = record.actions.get(action.idempotency_key)
        if prior is not None:
            if (
                prior["action_type"] != action.action_type.value
                or prior["target_type"] != action.target_type.value
                or prior["target_id"] != action.target_id
                or prior["interaction_id"] != action.interaction_id
                or prior["payload"] != action.payload
            ):
                raise PlanningConflict("idempotency key was reused with a different action")
            return record.current, prior.get("affected", {})

        self._expect_revision(record.current, action.plan_revision)

        if action.action_type == ActionType.SUBMIT:
            updated = self._apply_submit_action(record, action)
            affected: dict[str, Any] = {}
        elif action.action_type == ActionType.SKIP:
            updated = self._apply_skip_action(record, action)
            affected = {}
        elif action.action_type in _PLAN_REVISION_ACTIONS:
            updated, affected = self._apply_plan_revision_action(record, action)
        elif action.action_type == ActionType.REVISION_ROLLBACK:
            updated, affected = self._apply_rollback_action(record, action)
        else:
            raise PlanningPolicyError(f"action not implemented in this milestone: {action.action_type.value}")

        record.actions[action.idempotency_key] = {
            "action_type": action.action_type.value,
            "target_type": action.target_type.value,
            "target_id": action.target_id,
            "interaction_id": action.interaction_id,
            "payload": action.payload,
            "affected": affected,
        }
        return updated, affected

    def _apply_plan_revision_action(self, record: _PlanRecord, action: InteractionAction) -> tuple[PlanSnapshot, dict[str, Any]]:
        """执行假设/步骤/目标/约束的确定性修订动作，落新版本并保留当前状态。

        实时就地修订：只改内容、不改状态。审阅卡处于 awaiting_approval 时继续停留在
        awaiting_approval，用户改完即可直接“确认计划”，不再被强制打回 drafting。
        """
        try:
            changes, affected = compute_revision_changes(record.current, action)
        except (ValueError, LookupError) as exc:
            raise PlanningPolicyError(str(exc)) from None
        updated = self._revise(record.current, **changes)
        record.current = updated
        record.revisions.append(updated)
        self._event(
            record,
            PlanningEventType.PLAN_UPDATED,
            {"revision": updated.revision, "action": action.action_type.value, "affected": affected},
        )
        return updated, affected

    def _apply_rollback_action(self, record: _PlanRecord, action: InteractionAction) -> tuple[PlanSnapshot, dict[str, Any]]:
        try:
            target_revision = int(action.target_id or "")
        except ValueError as exc:
            raise PlanningPolicyError("revision_rollback requires a numeric revision") from None
        current = record.current
        if target_revision >= current.revision:
            raise PlanningConflict("cannot roll back to the current or a future revision")
        target = next((item for item in record.revisions if item.revision == target_revision), None)
        if target is None:
            raise PlanningNotFound("target revision not found")
        updated = revisions.rollback_snapshot(current, target)
        record.current = updated
        record.revisions.append(updated)
        self._event(
            record,
            PlanningEventType.PLAN_UPDATED,
            {"revision": updated.revision, "action": "revision_rollback", "rolled_back_to": target_revision},
        )
        return updated, {"rolled_back_to": target_revision}

    def _require_pending_interaction(self, record: _PlanRecord, action: InteractionAction) -> Interaction:
        if action.interaction_id is None:
            raise PlanningPolicyError("interaction-targeting action requires interaction_id")
        interaction = record.interactions.get(action.interaction_id)
        if interaction is None or interaction.plan_id != record.current.plan_id:
            raise PlanningNotFound("pending interaction not found")
        if interaction.status != InteractionStatus.PENDING:
            raise PlanningConflict("interaction is no longer pending")
        if interaction.plan_revision != record.current.revision:
            raise PlanningConflict("interaction belongs to a stale plan revision")
        return interaction

    def _apply_submit_action(self, record: _PlanRecord, action: InteractionAction) -> PlanSnapshot:
        interaction = self._require_pending_interaction(record, action)
        value = self._validate_submission_value(interaction, action.payload.get("value"))
        plan = record.current
        decisions = list(plan.decisions)
        key = self._decision_key(interaction)
        old = next((item for item in decisions if item.key == key), None)
        if old is not None and old.source == "user" and old.value != value:
            decisions = [item for item in decisions if item.key != key]
        decisions.append(PlanDecision(key=key, value=value, source="user", confidence=1.0))
        updated = self._revise(plan, status=PlanStatus.DRAFTING, decisions=decisions)
        record.current = updated
        record.revisions.append(updated)
        record.interactions[interaction.interaction_id] = interaction.model_copy(
            update={"status": InteractionStatus.SUBMITTED}
        )
        self._event(record, PlanningEventType.INTERACTION_SUBMITTED, {"interaction_id": interaction.interaction_id, "revision": updated.revision})
        self._event(record, PlanningEventType.PLAN_UPDATED, {"revision": updated.revision})
        return updated

    def _apply_skip_action(self, record: _PlanRecord, action: InteractionAction) -> PlanSnapshot:
        interaction = self._require_pending_interaction(record, action)
        record.interactions[interaction.interaction_id] = interaction.model_copy(
            update={"status": InteractionStatus.SKIPPED}
        )
        updated = self._revise(record.current, status=PlanStatus.DRAFTING)
        record.current = updated
        record.revisions.append(updated)
        self._event(record, PlanningEventType.INTERACTION_SKIPPED, {"interaction_id": interaction.interaction_id})
        self._event(record, PlanningEventType.PLAN_UPDATED, {"revision": updated.revision})
        return updated

    def approve(self, plan_id: str, *, owner_id: str, expected_revision: int) -> PlanSnapshot:
        record = self._owned(plan_id, owner_id)
        self._expect_revision(record.current, expected_revision)
        if record.current.status != PlanStatus.AWAITING_APPROVAL:
            raise PlanningConflict("plan is not awaiting approval")
        updated = self._revise(record.current, status=PlanStatus.APPROVED)
        record.current = updated
        record.revisions.append(updated)
        self._event(record, PlanningEventType.PLAN_APPROVED, {"revision": updated.revision})
        return updated

    def skip_interaction(
        self,
        plan_id: str,
        interaction_id: str,
        *,
        owner_id: str,
        expected_revision: int,
    ) -> PlanSnapshot:
        record = self._owned(plan_id, owner_id)
        self._expect_revision(record.current, expected_revision)
        interaction = record.interactions.get(interaction_id)
        if interaction is None or interaction.status != InteractionStatus.PENDING:
            raise PlanningNotFound("pending interaction not found")
        if interaction.plan_revision != expected_revision:
            raise PlanningConflict("interaction belongs to a stale plan revision")
        record.interactions[interaction_id] = interaction.model_copy(
            update={"status": InteractionStatus.SKIPPED}
        )
        updated = self._revise(record.current, status=PlanStatus.DRAFTING)
        record.current = updated
        record.revisions.append(updated)
        self._event(record, PlanningEventType.INTERACTION_SKIPPED, {"interaction_id": interaction_id})
        self._event(record, PlanningEventType.PLAN_UPDATED, {"revision": updated.revision})
        return updated

    def switch_mode(self, plan_id: str, *, owner_id: str, expected_revision: int, target_mode: str) -> PlanSnapshot:
        record = self._owned(plan_id, owner_id)
        self._expect_revision(record.current, expected_revision)
        if target_mode != "agent":
            raise PlanningPolicyError("handoff mode switch must target agent")
        if record.current.status != PlanStatus.APPROVED:
            raise PlanningConflict("only an approved plan can switch to Agent mode")
        updated = self._revise(record.current, status=PlanStatus.READY_FOR_HANDOFF)
        record.current = updated
        record.revisions.append(updated)
        self._event(record, PlanningEventType.HANDOFF_CREATED, {
            "handoff": revisions.build_handoff(updated).model_dump(mode="json"),
            "mode": "agent",
            "revision": updated.revision,
        })
        self._event(record, PlanningEventType.MODE_CHANGED, {"mode": "agent", "revision": updated.revision})
        return updated

    def authorize_execution(
        self,
        plan_id: str,
        *,
        owner_id: str,
        approved_revision: int,
        expected_revision: int,
        authorization_id: str,
    ) -> PlanSnapshot:
        record = self._owned(plan_id, owner_id)
        self._expect_revision(record.current, expected_revision)
        if not authorization_id or len(authorization_id) > 128:
            raise PlanningPolicyError("an execution authorization id is required")
        approved = next(
            (revision for revision in record.revisions if revision.revision == approved_revision),
            None,
        )
        if approved is None or approved.status != PlanStatus.APPROVED:
            raise PlanningConflict("authorization does not reference an approved plan revision")
        if record.current.status != PlanStatus.READY_FOR_HANDOFF:
            raise PlanningConflict("switching mode does not authorize execution")
        updated = self._revise(record.current, status=PlanStatus.EXECUTING)
        record.current = updated
        record.revisions.append(updated)
        self._event(record, PlanningEventType.RUN_STARTED, {"revision": updated.revision})
        return updated

    def events_after(self, plan_id: str, *, owner_id: str, sequence: int = 0) -> list[PlanningEvent]:
        if sequence < 0:
            raise ValueError("sequence must be non-negative")
        record = self._owned(plan_id, owner_id)
        return [event for event in record.events if event.sequence > sequence]

    @staticmethod
    def validate_plan_tools(registry: ToolRegistry, requested: list[str]) -> tuple[str, ...]:
        """Fail closed: requested tools must be in the server's read-only Plan set."""
        allowed: list[str] = []
        for name in requested:
            spec = registry.get(name)
            if name not in PLAN_TOOLS or spec is None or spec.risk_level != "low":
                raise PlanningPolicyError(f"tool is not allowed during planning: {name}")
            allowed.append(name)
        if len(allowed) != len(set(allowed)):
            raise PlanningPolicyError("duplicate planning tools are not allowed")
        return tuple(sorted(allowed))

    def _owned(self, plan_id: str, owner_id: str) -> _PlanRecord:
        record = self._plans.get(plan_id)
        if record is None or record.owner_id != owner_id:
            # Do not disclose whether another user's identifier exists.
            raise PlanningNotFound("plan not found")
        return record

    @staticmethod
    def _expect_revision(plan: PlanSnapshot, expected: int) -> None:
        if plan.revision != expected:
            raise PlanningConflict(f"revision conflict; latest revision is {plan.revision}")

    @staticmethod
    def _check_transition(current: PlanStatus, target: PlanStatus) -> None:
        if not can_transition_plan(current, target):
            raise PlanningConflict(f"invalid plan transition: {current.value} -> {target.value}")

    @staticmethod
    def _revise(plan: PlanSnapshot, **changes: Any) -> PlanSnapshot:
        now = datetime.now(UTC)
        data = plan.model_dump()
        data.update({"revision": plan.revision + 1, "updated_at": now, **changes})
        return PlanSnapshot.model_validate(data)

    def _event(self, record: _PlanRecord, event_type: PlanningEventType, payload: dict[str, Any]) -> None:
        event = PlanningEvent(
            event_id=f"evt_{uuid4().hex}",
            sequence=len(record.events) + 1,
            conversation_id=record.current.conversation_id,
            plan_id=record.current.plan_id,
            type=event_type,
            payload=payload,
            occurred_at=datetime.now(UTC),
        )
        record.events.append(event)

    @staticmethod
    def _decision_key(interaction: Interaction) -> str:
        explicit = interaction.constraints.get("decision_key")
        if explicit and isinstance(explicit, str) and explicit.strip():
            return explicit.strip()[:128]
        prompt = (interaction.prompt or "").strip()
        cleaned = "".join(ch for ch in prompt if ch.isalnum() or ch in " _-")
        cleaned = " ".join(cleaned.split())
        return (cleaned[:32] or interaction.interaction_id)[:128]

    @staticmethod
    def _validate_submission_value(interaction: Interaction, value: Any) -> Any:
        if interaction.type == InteractionType.SINGLE_SELECT:
            allowed = {item.id for item in interaction.options}
            if not isinstance(value, str) or value not in allowed:
                raise PlanningPolicyError("submission must match one of the offered options")
        elif interaction.type == InteractionType.MULTI_SELECT:
            allowed = {item.id for item in interaction.options}
            if not isinstance(value, list) or not value or len(value) > len(allowed):
                raise PlanningPolicyError("submission must be a non-empty list of offered options")
            if any(not isinstance(item, str) or item not in allowed for item in value) or len(value) != len(set(value)):
                raise PlanningPolicyError("submission contains an invalid or duplicate option")
        elif interaction.type in {InteractionType.TEXT_INPUT, InteractionType.TEXT_AREA}:
            max_length = interaction.constraints.get("max_length", 4000)
            if (
                isinstance(max_length, bool)
                or not isinstance(max_length, int)
                or max_length < 1
                or not isinstance(value, str)
                or not value.strip()
                or len(value) > min(max_length, 4000)
            ):
                raise PlanningPolicyError("text submission is empty or too long")
        elif interaction.type == InteractionType.FORM:
            if not isinstance(value, dict):
                raise PlanningPolicyError("form submission must be an object")
            fields = {field.key: field for field in (interaction.form or [])}
            if not fields:
                raise PlanningPolicyError("form interaction has no fields")
            cleaned: dict[str, Any] = {}
            for key, field in fields.items():
                raw = value.get(key)
                if field.required and (raw is None or (isinstance(raw, str) and not raw.strip()) or (isinstance(raw, list) and not raw)):
                    raise PlanningPolicyError(f"form field '{key}' is required")
                if raw is None:
                    continue
                cleaned[key] = PlanningOrchestrator._validate_form_field(field, raw)
            return cleaned
        elif interaction.type == InteractionType.CONFIRMATION:
            if not isinstance(value, bool):
                raise PlanningPolicyError("confirmation submission must be boolean")
        else:
            raise PlanningPolicyError("interaction type does not accept a user submission")
        return value

    @staticmethod
    def _validate_form_field(field: FormField, raw: Any) -> Any:
        if field.type == "single_select":
            allowed = {item.id for item in field.options}
            if not isinstance(raw, str) or raw not in allowed:
                raise PlanningPolicyError(f"form field '{field.key}' must match one of the offered options")
            return raw
        if field.type == "multi_select":
            allowed = {item.id for item in field.options}
            if not isinstance(raw, list) or not raw or len(raw) > len(allowed):
                raise PlanningPolicyError(f"form field '{field.key}' must be a non-empty list of offered options")
            if any(not isinstance(item, str) or item not in allowed for item in raw) or len(raw) != len(set(raw)):
                raise PlanningPolicyError(f"form field '{field.key}' contains an invalid or duplicate option")
            return raw
        if field.type in {"text", "textarea"}:
            max_length = field.constraints.get("max_length", 4000)
            if not isinstance(raw, str) or not raw.strip():
                raise PlanningPolicyError(f"form field '{field.key}' must be non-empty text")
            if isinstance(max_length, int) and max_length >= 1 and len(raw) > min(max_length, 4000):
                raise PlanningPolicyError(f"form field '{field.key}' is too long")
            return raw
        if field.type == "number":
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise PlanningPolicyError(f"form field '{field.key}' must be a number")
            return raw
        raise PlanningPolicyError(f"form field '{field.key}' has an unsupported type")


class InteractionOrchestrator:
    """M5：把 LLM 的交互编排变成受约束的编排决策。

    输入为 DecisionProposal（模型自由发挥）与 PlanSnapshot（阶段/约束/历史），在
    服务端落地前做白名单与阶段约束校验。组件/动作白名单由契约层
    （InteractionType/ActionType/ACTION_TARGETS）提供，这里补充数量与阶段约束，
    确保交互既不是"固定问卷"，也不是"模型自由发挥"。
    """

    MAX_ASSUMPTIONS_PER_TURN = 20
    MAX_OPTIONS = 20
    MAX_STEPS = 100

    def __init__(self) -> None:
        self.last_verdict: dict[str, Any] = {}

    def evaluate(self, proposal: DecisionProposal, plan: PlanSnapshot) -> dict[str, Any]:
        problems: list[str] = []
        component: str | None = None
        if proposal.action == DecisionAction.ASK_USER:
            if proposal.interaction is None:
                problems.append("ask_user requires an interaction")
            else:
                component = str(proposal.interaction.type)
                selection = proposal.interaction.type in {"single_select", "multi_select"}
                if selection and not proposal.interaction.options:
                    problems.append("selection interaction requires options")
                if proposal.interaction.type == "form":
                    if not proposal.interaction.form:
                        problems.append("form interaction requires form fields")
                    elif len(proposal.interaction.form) > self.MAX_OPTIONS:
                        problems.append("too many form fields in one interaction")
                if len(proposal.interaction.options) > self.MAX_OPTIONS:
                    problems.append("too many options in one interaction")
        elif proposal.action == DecisionAction.REQUEST_APPROVAL:
            component = "plan_review"
            if not proposal.steps:
                problems.append("approval requires plan steps")
            if len(proposal.steps) > self.MAX_STEPS:
                problems.append("too many steps")
        elif proposal.action == DecisionAction.UPDATE_PLAN:
            component = "plan_editor"
        elif proposal.action == DecisionAction.RESPOND:
            component = "text"

        if len(proposal.assumptions) > self.MAX_ASSUMPTIONS_PER_TURN:
            problems.append("too many assumptions in one turn")

        verdict = {
            "action": proposal.action.value,
            "component": component,
            "plan_stage": plan.status.value,
            "valid": not problems,
            "problems": problems,
        }
        self.last_verdict = verdict
        return verdict

    def enforce(self, proposal: DecisionProposal, plan: PlanSnapshot) -> None:
        verdict = self.evaluate(proposal, plan)
        if not verdict["valid"]:
            raise PlanningPolicyError("orchestrator rejected proposal: " + "; ".join(verdict["problems"]))


class PlanningDecisionEngine:
    """Ask the shared LLM for a structured proposal; never execute its actions."""

    def __init__(self, provider: LLMProvider, *, model: str, timeout_seconds: float = 60) -> None:
        self._provider = provider
        self._model = model
        self._timeout_seconds = timeout_seconds
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.reasoning_tokens = 0
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.last_messages: list[dict[str, Any]] = []
        self.last_output_text = ""

    def _note_usage(self, usage) -> None:
        if usage is None:
            return
        self.prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
        self.completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
        self.total_tokens += int(getattr(usage, "total_tokens", 0) or 0)
        self.reasoning_tokens += int(getattr(usage, "reasoning_tokens", 0) or 0)
        self.cache_read_tokens += int(getattr(usage, "cache_read_tokens", 0) or 0)
        self.cache_write_tokens += int(getattr(usage, "cache_write_tokens", 0) or 0)

    def _proposal_request(
        self, *, goal: str, user_message: str, plan: PlanSnapshot,
        history: list[dict[str, str]] | None = None,
    ) -> ProviderChatRequest:
        proposal_schema = DecisionProposal.model_json_schema()
        proposal_schema["$defs"]["DecisionAction"]["enum"] = [
            DecisionAction.RESPOND.value,
            DecisionAction.ASK_USER.value,
            DecisionAction.UPDATE_PLAN.value,
            DecisionAction.REQUEST_APPROVAL.value,
        ]
        system_prompt = (
            "You are an expert planning assistant for a chat app. You help users turn a vague "
            "need into a clear, actionable plan through natural, focused conversation. When you "
            "need information from the user, ask it through the app's registered UI interaction "
            "components (listed below) — not as a wall of questions in plain chat text.\n\n"
            "Return exactly one JSON object conforming to this schema (the server validates it "
            "strictly):\n"
            f"{json.dumps(proposal_schema, ensure_ascii=False, separators=(',', ':'))}\n\n"
            "Choose action from respond, ask_user, update_plan, request_approval. Never choose "
            "ready_for_handoff (approval, mode switch, and execution authorization are separate "
            "user actions).\n\n"
            "REGISTERED UI INTERACTION COMPONENTS — when you ask the user for information "
            "(action=\"ask_user\"), put the question(s) into the interaction card using ONE of "
            "these registered component types. The client renders only these; any other "
            "interaction.type is rejected, and a question left only in the response text will not "
            "be collectable:\n"
            "  · single_select — pick one of a small set of options (radio group).\n"
            "  · multi_select — pick some options from a set (checkboxes).\n"
            "  · text_input — one short single-line answer.\n"
            "  · text_area — one longer multi-line answer.\n"
            "  · confirmation — a yes/no gate.\n"
            "  · form — collect SEVERAL related hard constraints in one card; each field is "
            "text / textarea / number / single_select / multi_select.\n"
            "Write prompt, option labels, descriptions, and form field labels in the user's "
            "language.\n\n"
            "STEP 0 — Read the message like a person, not a form:\n"
            "If the message is a pure greeting (like \"你好\"), small talk, thanks, or a plain "
            "question with no deliverable and no emotional distress, reply with action=\"respond\": "
            "a short, warm, natural reply in the user's language that gently invites them to say "
            "what they want to plan. Do NOT use ask_user, do NOT emit a decision card, do NOT "
            "propose steps/decisions/assumptions, do NOT request approval. Leave goal, assumptions, "
            "proposed_decisions, steps, and interaction empty.\n\n"
            "If the message expresses emotional distress or a dilemma (\"我很难过\", \"我很焦虑\", "
            "\"我很矛盾\", \"我不知道该怎么办\"), do NOT just console them in plain text. Use "
            "action=\"ask_user\" with a single_select interaction offering exactly 4 options so the "
            "user can pick without typing. Suggested options (write them in the user's language, "
            "you may adapt the wording but keep 4):\n"
            "  1) 想找个人说说发生了什么\n"
            "  2) 帮我梳理一下现在纠结的点\n"
            "  3) 只需要一点安慰和陪伴\n"
            "  4) 暂时不想细说，先陪陪我\n"
            "Keep the response warm, short, and non-pressuring; the user may skip. Do NOT propose "
            "steps or request approval yet.\n\n"
            "Only when the message expresses a concrete goal do you proceed to planning below.\n\n"
            "How to plan each turn:\n"
            "1. Before asking anything, classify every unknown fact into ONE of three buckets:\n"
            "   - HARD CONSTRAINT: something that changes the plan's structure, feasibility, scope, "
            "cost, or safety if it is different. Examples: for travel, 出发地/交通方式/预算/同行人/天数; "
            "for a system design, 技术栈/规模/权限模型/合规要求; for an event, 日期/人数/预算/场地. "
            "An unresolved hard constraint must be ASKED — never silently assume it.\n"
            "   - SOFT PREFERENCE: a taste that shapes details but not the overall structure (e.g. "
            "which scenic spots or which cuisine). Ask once with a multi_select, or assume and disclose.\n"
            "   - TRIVIA: naming, colors, minor defaults that do not change the outcome. Always assume.\n"
            "Choose the interaction type that fits (see REGISTERED UI INTERACTION COMPONENTS "
            "above): group SEVERAL related hard constraints into one form card (e.g. 出发地 + 交通 + "
            "预算 + 同行人), otherwise use the single/multi/confirmation/text type that matches. "
            "Ask only what is needed to proceed this turn. Write visible text in the user's "
            "language.\n"
            "2. The FIRST time the user expresses a clear goal, write it into the \"goal\" field "
            "even when the action is ask_user or respond, so the server records the real target "
            "instead of the user's opening words.\n"
            "3. NEVER ask trivia (naming, colors, minor defaults) — always assume those. NEVER "
            "silently assume a HARD CONSTRAINT (rule 1): if a hard constraint is still unknown and "
            "it would change the plan, ask it — alone or grouped into a form. Only if you must "
            "proceed without it, put it in assumptions AND flag it explicitly in your response so "
            "the user sees it before approving.\n"
            "4. Decisions you settle yourself go into proposed_decisions with source \"assumed\" "
            "(or \"system\" for fixed facts). proposed_decisions[].source accepts ONLY \"assumed\" "
            "or \"system\" — never \"user\". The server records the user's answered questions with "
            "source \"user\" on its own; never mark an assumed decision as user-confirmed.\n"
            "5. Choose request_approval ONLY when every material HARD CONSTRAINT is resolved "
            "(answered by the user, or explicitly listed in assumptions and flagged in your "
            "response). If any hard constraint is still unknown and un-flagged, keep asking "
            "instead. A request_approval proposal needs a concrete goal, 4-8 implementation-ready "
            "steps (id/title/description/depends_on), and a short response that summarizes the "
            "plan AND explicitly flags which parts are still assumptions the user should review.\n"
            "6. For ask_user, include a human-readable decision_key in the interaction constraints "
            "(e.g. \"tech_stack\", \"hosting\") so the answer is stored under a meaningful name. "
            "For a form, give each field a stable, semantic key (e.g. \"departure\", \"transport\", "
            "\"budget\", \"party\") and set decision_key to a meaningful group name.\n"
            "7. For ask_user return one interaction (type, prompt, optional description, options "
            "for selection types, form for a form, optional constraints). Do NOT include "
            "protocol_version, interaction_id, plan_id, plan_revision, status, or plan_review — "
            "the server supplies lifecycle fields.\n"
            "8. A request_approval proposal must omit interaction (the server creates the review "
            "card). For update_plan, explain the draft changes in response. Planning is read-only; "
            "never request or claim tool execution.\n"
            "9. Write the JSON keys in this exact order: protocol_version, response, action, goal, "
            "assumptions, proposed_decisions, steps, interaction. Keep response near the TOP "
            "(right after protocol_version) so it streams to the user immediately; never put "
            "response last.\n"
            "10. State low-risk assumptions in assumptions (plain text) too, so the review card "
            "lists them separately from confirmed decisions. Use confirmed decisions and history; "
            "do not repeat answered or skipped questions."
        )
        return ProviderChatRequest(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"goal": goal, "plan_status": plan.status.value,
                         "latest_message": user_message,
                         "note": "goal may still be the user's opening words and not the real target "
                                 "yet; treat it as the target only if it actually expresses a task.",
                         "plan": plan.model_dump(mode="json"),
                         "conversation_history": history or []},
                        ensure_ascii=False,
                    ),
                },
            ],
            temperature=0,
            max_tokens=8000,
            response_format={"type": "json_object"},
        )

    @staticmethod
    def _parse_proposal(content: str) -> DecisionProposal:
        raw = _load_proposal_json(content)
        if not isinstance(raw, dict):
            logger.warning("planning proposal rejected request_id=%s fields=invalid_json raw=%s",
                           get_request_id(), (content or "")[:1200].replace("\n", " "))
            raise PlanningPolicyError("planning model returned an invalid proposal (output was not a single JSON object)")
        # 容错：模型偶尔把 proposed_decisions 标成 source="user"（非法值，会整体拒绝）。
        # 归一化为 "assumed"——用户确认的决策本就由服务器以 source="user" 记录。
        for decision in raw.get("proposed_decisions") or []:
            if isinstance(decision, dict) and decision.get("source") == "user":
                decision["source"] = "assumed"
        try:
            return DecisionProposal.model_validate(raw)
        except ValidationError as exc:
            invalid_fields = ",".join(".".join(str(part) for part in issue["loc"]) for issue in exc.errors())
            logger.warning("planning proposal rejected request_id=%s fields=%s", get_request_id(), invalid_fields)
            raise PlanningPolicyError(f"planning model returned an invalid proposal (schema invalid at fields: {invalid_fields})") from None

    async def propose(
        self, *, goal: str, user_message: str, plan: PlanSnapshot,
        history: list[dict[str, str]] | None = None,
    ) -> DecisionProposal:
        request = self._proposal_request(goal=goal, user_message=user_message, plan=plan, history=history)
        self.last_messages = list(request.messages)
        try:
            result: CompletionResult = await asyncio.wait_for(
                self._provider.create_chat_completion(request),
                timeout=self._timeout_seconds,
            )
            self._note_usage(result.usage)
        except TimeoutError:
            raise PlanningPolicyError("planning model request timed out") from None
        if result.tool_calls:
            raise PlanningPolicyError("planning proposal cannot request tool execution")
        if not result.content:
            raise PlanningPolicyError("planning model returned an empty proposal")
        self.last_output_text = result.content
        try:
            return self._parse_proposal(result.content)
        except PlanningPolicyError as parse_error:
            # 一次校正重试：把失败输出与校验错误回喂给模型，多数偶发失败可借此恢复。
            corrected = await self._retry_proposal(request, result.content, str(parse_error))
            if corrected is not None:
                return corrected
            raise

    async def _retry_proposal(
        self, request: ProviderChatRequest, raw_output: str, error: str,
    ) -> DecisionProposal | None:
        """把失败的原始输出与校验错误回喂给模型，请求输出一次修正后的 JSON。

        仅作为偶发失败的自愈手段；仍失败时返回 None，由调用方按原错误处理。
        """
        correction = (
            "Your previous response could not be validated as a planning proposal.\n"
            f"Validation error: {error}\n"
            "Output ONLY a corrected JSON object that strictly conforms to the schema. "
            "Do not wrap it in markdown fences and do not add any commentary."
        )
        retry_request = ProviderChatRequest(
            model=request.model,
            messages=[
                *request.messages,
                {"role": "assistant", "content": raw_output},
                {"role": "user", "content": correction},
            ],
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            reasoning_effort=request.reasoning_effort,
            response_format=request.response_format,
        )
        try:
            result: CompletionResult = await asyncio.wait_for(
                self._provider.create_chat_completion(retry_request),
                timeout=self._timeout_seconds,
            )
            self._note_usage(result.usage)
        except Exception as exc:  # 校正重试是最佳努力，任何异常都退回到原错误。
            logger.warning("planning retry failed request_id=%s type=%s", get_request_id(), type(exc).__name__)
            return None
        if result.tool_calls or not result.content:
            return None
        try:
            return self._parse_proposal(result.content)
        except PlanningPolicyError:
            return None

    async def propose_stream(
        self, *, goal: str, user_message: str, plan: PlanSnapshot,
        history: list[dict[str, str]] | None = None,
    ):
        """流式生成决策提案。逐段产出 response 文本（delta），最终产出完整 proposal。

        yield 的元组：("delta", str) | ("done", DecisionProposal) | ("error", str)。
        """
        request = self._proposal_request(goal=goal, user_message=user_message, plan=plan, history=history)
        self.last_messages = list(request.messages)
        buffer = ""
        last_len = 0
        action_announced = False
        last_usage = None
        try:
            async for chunk in self._provider.stream_chat_completion(request):
                if chunk is not None and chunk.usage is not None:
                    last_usage = chunk.usage
                if not chunk or not chunk.content:
                    continue
                buffer += chunk.content
                preview = _extract_response_preview(buffer)
                if len(preview) > last_len:
                    yield ("delta", preview[last_len:])
                    last_len = len(preview)
                if not action_announced:
                    match = _ACTION_FIELD.search(buffer)
                    if match and match.group(1) in ("ask_user", "request_approval"):
                        action_announced = True
                        yield ("interaction_pending", match.group(1))
        except Exception as exc:
            self._note_usage(last_usage)
            logger.warning("planning stream failed request_id=%s type=%s", get_request_id(), type(exc).__name__)
            yield ("error", "PLANNING_UPSTREAM_FAILED")
            return
        self._note_usage(last_usage)
        if not buffer.strip():
            yield ("error", "PLANNING_POLICY_REJECTED")
            return
        try:
            proposal = self._parse_proposal(buffer)
        except PlanningPolicyError as parse_error:
            # 一次校正重试。成功后前端会以 done 里的 response 覆盖已流出的文字。
            corrected = await self._retry_proposal(request, buffer, str(parse_error))
            if corrected is None:
                yield ("error", "PLANNING_POLICY_REJECTED")
                return
            proposal = corrected
        self.last_output_text = buffer
        yield ("done", proposal)


_RESPONSE_FULL = re.compile(r'"response"\s*:\s*"((?:[^"\\]|\\.)*)"')
_RESPONSE_OPEN = re.compile(r'"response"\s*:\s*"((?:[^"\\]|\\.)*)$')
_ACTION_FIELD = re.compile(r'"action"\s*:\s*"([a-z_]+)"')


def _load_proposal_json(content: str) -> dict | None:
    """把模型输出解析为提案 dict，容忍 markdown 围栏、前后噪音与截断。

    流式规划输出理论上应为一个纯 JSON 对象，但模型偶尔会：
      - 用 ```json ... ``` 围栏包裹；
      - 在 JSON 前后附加说明文字；
      - 因 max_tokens 截断导致 JSON 不完整。

    这里依次尝试：直接解析 → 剥离围栏后解析 → 截取最外层对象并修复截断。
    解析成功返回 dict，否则返回 None。
    """
    if not content:
        return None
    text = content.strip().lstrip("\ufeff")

    # 1) 直接解析。
    parsed = _try_json_loads(text)
    if isinstance(parsed, dict):
        return parsed

    # 2) 剥离 markdown 代码围栏。
    fence = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence:
        parsed = _try_json_loads(fence.group(1).strip())
        if isinstance(parsed, dict):
            return parsed

    # 3) 截取最外层 { ... } 并修复截断后解析。
    start = text.find("{")
    if start != -1:
        end = text.rfind("}")
        if end != -1 and end > start:
            slice_ = text[start:end + 1]
        else:
            # 没有闭合括号：可能被截断，取剩余部分后尝试修复。
            slice_ = text[start:]
        parsed = _try_json_loads(slice_)
        if isinstance(parsed, dict):
            return parsed
        repaired = _repair_truncated_json(slice_)
        parsed = _try_json_loads(repaired)
        if isinstance(parsed, dict):
            return parsed

    return None


def _try_json_loads(text: str) -> dict | None:
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _repair_truncated_json(text: str) -> str:
    """尽力修复被截断的 JSON 对象：闭合未闭合的字符串与对象/数组括号。"""
    result: list[str] = []
    in_string = False
    escaped = False
    stack: list[str] = []
    for char in text:
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            result.append(char)
        elif char in "{[":
            stack.append(char)
            result.append(char)
        elif char in "}]":
            if stack and ((stack[-1] == "{" and char == "}") or (stack[-1] == "[" and char == "]")):
                stack.pop()
            result.append(char)
        else:
            result.append(char)
    if in_string:
        result.append('"')
    for opener in reversed(stack):
        result.append("}" if opener == "{" else "]")
    return "".join(result)


def _extract_response_preview(buffer: str) -> str:
    """从尚未完成的 JSON 流中提取 response 字段当前文本，用于打字机预览。"""
    match = _RESPONSE_FULL.search(buffer)
    if match is None:
        match = _RESPONSE_OPEN.search(buffer)
    if match is None:
        return ""
    return _unescape_json_string(match.group(1))


def _unescape_json_string(raw: str) -> str:
    out: list[str] = []
    i = 0
    simple = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "\\": "\\", '"': '"', "/": "/"}
    while i < len(raw):
        char = raw[i]
        if char == "\\" and i + 1 < len(raw):
            nxt = raw[i + 1]
            if nxt == "u" and i + 5 < len(raw):
                try:
                    out.append(chr(int(raw[i + 2:i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            out.append(simple.get(nxt, nxt))
            i += 2
        else:
            out.append(char)
            i += 1
    return "".join(out)
