"""M7-T03 — Plan 接入：交互式需求收集、方案 revision、审批与交接。

覆盖：全链路状态机与事件可追溯、交接快照是计划权威状态的投影（不创建平行真相源）、
未审批不得交接、未交接不得授权执行、跨用户越权、空步骤等异常路径。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.planning import revisions
from app.planning.contracts import InteractionSubmission, PlanSnapshot, PlanStatus, PlanningEventType
from app.planning.runtime import (
    DecisionAction,
    DecisionProposal,
    PlanningConflict,
    PlanningNotFound,
    PlanningOrchestrator,
    PlanningPolicyError,
)

GOAL = "Design permissions"
STEPS = [{"id": "step_1", "title": "Define roles"}]


def _plan(planner: PlanningOrchestrator, goal: str = GOAL) -> PlanSnapshot:
    return planner.create_plan(owner_id="user-a", conversation_id="conv-a", goal=goal)


def _draft(planner: PlanningOrchestrator, plan: PlanSnapshot) -> PlanSnapshot:
    return planner.apply_proposal(
        plan.plan_id,
        DecisionProposal(
            protocol_version="1.0",
            action=DecisionAction.UPDATE_PLAN,
            goal=GOAL,
            steps=STEPS,
        ),
        owner_id="user-a",
        expected_revision=plan.revision,
    )


def _approve(planner: PlanningOrchestrator, plan: PlanSnapshot) -> PlanSnapshot:
    drafting = _draft(planner, plan)
    review = planner.apply_proposal(
        plan.plan_id,
        DecisionProposal(
            protocol_version="1.0",
            action=DecisionAction.REQUEST_APPROVAL,
            goal=GOAL,
            steps=STEPS,
        ),
        owner_id="user-a",
        expected_revision=drafting.revision,
    )
    return planner.approve(plan.plan_id, owner_id="user-a", expected_revision=review.revision)


def _handoff(planner: PlanningOrchestrator, approved: PlanSnapshot) -> PlanSnapshot:
    return planner.switch_mode(
        approved.plan_id, owner_id="user-a", expected_revision=approved.revision, target_mode="agent"
    )


def _event_types(planner: PlanningOrchestrator, plan_id: str) -> list[str]:
    return [event.type.value for event in planner.events_after(plan_id, owner_id="user-a")]


# ---------------------------------------------------------------------------
# 交接快照：PlanHandoff 是计划权威状态的投影，不创建平行真相源
# ---------------------------------------------------------------------------

def test_handoff_snapshot_is_a_projection_of_the_approved_plan() -> None:
    planner = PlanningOrchestrator()
    plan = _plan(planner)
    approved = _approve(planner, plan)

    handoff = revisions.build_handoff(approved)

    assert handoff.approved_plan_id == approved.plan_id
    assert handoff.approved_revision == approved.revision
    assert handoff.conversation_id == approved.conversation_id
    assert handoff.goal == approved.goal
    assert handoff.constraints == approved.constraints
    assert [item.id for item in handoff.steps] == [item.id for item in approved.steps]
    assert [item.title for item in handoff.steps] == [item.title for item in approved.steps]
    # 交接并不授予执行权限；Agent 执行前仍需重新校验。
    assert handoff.authorization == "revalidate_at_execution"


def test_handoff_carries_confirmed_decisions_from_plan() -> None:
    planner = PlanningOrchestrator()
    plan = _plan(planner)
    # 需求收集：先落一条用户决策，再审批。
    pending = planner.apply_proposal(
        plan.plan_id,
        DecisionProposal(
            protocol_version="1.0",
            action=DecisionAction.ASK_USER,
            response="Which permission model?",
            interaction={
                "type": "single_select",
                "prompt": "Choose a permission model",
                "options": [{"id": "rbac", "label": "RBAC"}, {"id": "abac", "label": "ABAC"}],
                "constraints": {"decision_key": "permission_model"},
            },
        ),
        owner_id="user-a",
        expected_revision=plan.revision,
    )
    interaction_id = planner.events_after(plan.plan_id, owner_id="user-a")[-1].payload["interaction_id"]
    submitted = planner.submit_interaction(
        plan.plan_id,
        InteractionSubmission(
            interaction_id=interaction_id,
            expected_plan_revision=pending.revision,
            client_submission_id="handoff_submit_01",
            value="rbac",
        ),
        owner_id="user-a",
    )
    approved = _approve(planner, submitted)

    handoff = revisions.build_handoff(approved)
    assert handoff.confirmed_decisions["permission_model"] == "rbac"


def test_build_handoff_rejects_plan_without_steps() -> None:
    plan = PlanSnapshot.model_validate(
        {
            "protocol_version": "1.0",
            "conversation_id": "conv-a",
            "plan_id": "plan_01",
            "revision": 0,
            "status": "approved",
            "goal": GOAL,
            "created_at": "2026-11-14T00:00:00+00:00",
            "updated_at": "2026-11-14T00:00:00+00:00",
        }
    )
    # 交接协议要求至少一个步骤；空步骤是明确异常路径。
    with pytest.raises(ValidationError):
        revisions.build_handoff(plan)


# ---------------------------------------------------------------------------
# 全链路：需求收集 → 审批 → 交接 → 授权执行，事件可追溯
# ---------------------------------------------------------------------------

def test_full_plan_journey_emits_handoff_and_mode_events() -> None:
    planner = PlanningOrchestrator()
    plan = _plan(planner)
    approved = _approve(planner, plan)
    ready = _handoff(planner, approved)
    executing = planner.authorize_execution(
        plan.plan_id,
        owner_id="user-a",
        approved_revision=approved.revision,
        expected_revision=ready.revision,
        authorization_id="auth_handoff_01",
    )

    assert executing.status == PlanStatus.EXECUTING
    types = _event_types(planner, plan.plan_id)
    assert "plan.created" in types
    assert "plan.approved" in types
    assert "handoff.created" in types
    assert "conversation.mode_changed" in types
    assert "run.started" in types
    # 交接事件先于模式切换、模式切换先于授权执行，链路有序。
    assert types.index("handoff.created") < types.index("conversation.mode_changed")
    assert types.index("conversation.mode_changed") < types.index("run.started")


def test_handoff_event_payload_is_structured_and_versioned() -> None:
    planner = PlanningOrchestrator()
    plan = _plan(planner)
    approved = _approve(planner, plan)
    _handoff(planner, approved)

    event = next(
        item for item in planner.events_after(plan.plan_id, owner_id="user-a")
        if item.type == PlanningEventType.HANDOFF_CREATED
    )
    payload = event.payload
    assert payload["mode"] == "agent"
    assert payload["revision"] == approved.revision + 1
    handoff = payload["handoff"]
    assert handoff["protocol_version"] == "1.0"
    assert handoff["approved_plan_id"] == plan.plan_id
    assert handoff["authorization"] == "revalidate_at_execution"
    assert handoff["steps"] == [{"id": "step_1", "title": "Define roles"}]


# ---------------------------------------------------------------------------
# 异常路径：审批/交接/授权的门禁与越权
# ---------------------------------------------------------------------------

def test_switch_mode_rejects_unapproved_plan() -> None:
    planner = PlanningOrchestrator()
    plan = _plan(planner)
    drafting = _draft(planner, plan)
    assert drafting.status == PlanStatus.DRAFTING
    with pytest.raises(PlanningConflict, match="only an approved plan"):
        planner.switch_mode(
            plan.plan_id, owner_id="user-a", expected_revision=drafting.revision, target_mode="agent"
        )


def test_switch_mode_rejects_non_agent_target() -> None:
    planner = PlanningOrchestrator()
    plan = _plan(planner)
    approved = _approve(planner, plan)
    with pytest.raises(PlanningPolicyError, match="target agent"):
        planner.switch_mode(
            plan.plan_id, owner_id="user-a", expected_revision=approved.revision, target_mode="ask"
        )


def test_authorize_execution_rejects_before_handoff() -> None:
    planner = PlanningOrchestrator()
    plan = _plan(planner)
    approved = _approve(planner, plan)
    # 已审批但未切换（未进入 ready_for_handoff），直接授权执行应被拒绝。
    with pytest.raises(PlanningConflict, match="switching mode does not authorize"):
        planner.authorize_execution(
            plan.plan_id,
            owner_id="user-a",
            approved_revision=approved.revision,
            expected_revision=approved.revision,
            authorization_id="auth_handoff_02",
        )


def test_authorize_execution_rejects_wrong_approved_revision() -> None:
    planner = PlanningOrchestrator()
    plan = _plan(planner)
    approved = _approve(planner, plan)
    ready = _handoff(planner, approved)
    # 引用了不存在的已审批版本。
    with pytest.raises(PlanningConflict, match="approved plan revision"):
        planner.authorize_execution(
            plan.plan_id,
            owner_id="user-a",
            approved_revision=999,
            expected_revision=ready.revision,
            authorization_id="auth_handoff_03",
        )


def test_cross_owner_handoff_is_rejected() -> None:
    planner = PlanningOrchestrator()
    plan = _plan(planner)
    approved = _approve(planner, plan)
    with pytest.raises(PlanningNotFound):
        planner.switch_mode(
            plan.plan_id, owner_id="user-b", expected_revision=approved.revision, target_mode="agent"
        )
