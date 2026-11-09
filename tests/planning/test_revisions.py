"""M3（假设）与 M4（步骤）确定性修订逻辑测试。"""

from __future__ import annotations

import pytest

from app.planning import revisions
from app.planning.contracts import (
    ActionType,
    Assumption,
    AssumptionStatus,
    InteractionAction,
    PlanSnapshot,
    PlanStatus,
    PlanStep,
)
from app.planning.runtime import (
    DecisionAction,
    DecisionProposal,
    PlanningConflict,
    PlanningOrchestrator,
    PlanningPolicyError,
)


def _drafting_plan() -> tuple[PlanningOrchestrator, str, PlanSnapshot]:
    planner = PlanningOrchestrator()
    plan = planner.create_plan(owner_id="user-a", conversation_id="conv-a", goal="Build a blog")
    updated = planner.apply_proposal(
        plan.plan_id,
        DecisionProposal(
            protocol_version="1.0",
            action=DecisionAction.UPDATE_PLAN,
            goal="Build a blog",
            assumptions=["Use PostgreSQL", "Deploy on single server"],
            steps=[
                {"id": "step_1", "title": "Set up PostgreSQL database", "depends_on": []},
                {"id": "step_2", "title": "Build API", "depends_on": ["step_1"]},
                {"id": "step_3", "title": "Deploy", "depends_on": ["step_2"]},
            ],
        ),
        owner_id="user-a",
        expected_revision=plan.revision,
    )
    return planner, plan.plan_id, updated


def _action(plan_id: str, revision: int, action_type: str, target_type: str, target_id: str | None, payload: dict, key: str) -> InteractionAction:
    return InteractionAction.model_validate(
        {
            "plan_id": plan_id,
            "plan_revision": revision,
            "component_type": "assumption_editor",
            "action_type": action_type,
            "target_type": target_type,
            "target_id": target_id,
            "payload": payload,
            "idempotency_key": key,
        }
    )


# ---------------------------------------------------------------------------
# M3：结构化假设
# ---------------------------------------------------------------------------

def test_assumptions_are_synced_with_stable_ids() -> None:
    _, _, plan = _drafting_plan()
    assert [item.text for item in plan.assumption_items] == ["Use PostgreSQL", "Deploy on single server"]
    assert [item.id for item in plan.assumption_items] == ["asm_0", "asm_1"]
    # 文本与纯文本层保持一致。
    assert plan.assumptions == ["Use PostgreSQL", "Deploy on single server"]


def test_edit_assumption_updates_text_and_reports_affected_steps() -> None:
    planner, plan_id, plan = _drafting_plan()
    action = _action(plan_id, plan.revision, "assumption_edit", "assumption", "asm_0",
                     {"text": "Use MySQL"}, "client_edit_0001")
    updated, _ = planner.apply_action(plan_id, action, owner_id="user-a")
    item = next(item for item in updated.assumption_items if item.id == "asm_0")
    assert item.text == "Use MySQL"
    assert item.status == AssumptionStatus.CONFIRMED
    assert updated.revision == plan.revision + 1
    # 影响分析：旧文本 "Use PostgreSQL" 命中标题含 "PostgreSQL" 的 step_1。
    events = planner.events_after(plan_id, owner_id="user-a")
    assert events[-1].payload["affected"]["affected_steps"] == ["step_1"]


def test_edit_assumption_affects_decision_mentioning_text() -> None:
    planner = PlanningOrchestrator()
    plan = planner.create_plan(owner_id="user-a", conversation_id="conv-a", goal="Build a blog")
    updated = planner.apply_proposal(
        plan.plan_id,
        DecisionProposal(
            protocol_version="1.0",
            action=DecisionAction.UPDATE_PLAN,
            goal="Build a blog",
            assumptions=["Use PostgreSQL"],
            proposed_decisions=[{"key": "db", "value": "PostgreSQL 16", "source": "assumed"}],
            steps=[{"id": "step_1", "title": "Set up database"}],
        ),
        owner_id="user-a",
        expected_revision=plan.revision,
    )
    action = _action(plan.plan_id, updated.revision, "assumption_edit", "assumption", "asm_0",
                     {"text": "Use MySQL"}, "client_edit_0002")
    updated2, _ = planner.apply_action(plan.plan_id, action, owner_id="user-a")
    events = planner.events_after(plan.plan_id, owner_id="user-a")
    assert events[-1].payload["affected"]["affected_decisions"] == ["db"]


def test_reject_assumption_removes_from_text_layer() -> None:
    planner, plan_id, plan = _drafting_plan()
    action = _action(plan_id, plan.revision, "assumption_reject", "assumption", "asm_1",
                     {}, "client_reject_01")
    updated, _ = planner.apply_action(plan_id, action, owner_id="user-a")
    item = next(item for item in updated.assumption_items if item.id == "asm_1")
    assert item.status == AssumptionStatus.REJECTED
    assert "Deploy on single server" not in updated.assumptions


def test_confirm_assumption_marks_confirmed() -> None:
    planner, plan_id, plan = _drafting_plan()
    action = _action(plan_id, plan.revision, "assumption_confirm", "assumption", "asm_0",
                     {}, "client_confirm_01")
    updated, _ = planner.apply_action(plan_id, action, owner_id="user-a")
    item = next(item for item in updated.assumption_items if item.id == "asm_0")
    assert item.status == AssumptionStatus.CONFIRMED


# ---------------------------------------------------------------------------
# M4：步骤修订
# ---------------------------------------------------------------------------

def test_reorder_steps_respects_dependency_order() -> None:
    planner, plan_id, plan = _drafting_plan()
    # 非法顺序：把 step_3 放到它依赖的 step_2 之前。
    bad = _action(plan_id, plan.revision, "step_reorder", "step", None,
                  {"ordered_ids": ["step_1", "step_3", "step_2"]}, "client_reorder_bad")
    with pytest.raises(PlanningPolicyError, match="must come after"):
        planner.apply_action(plan_id, bad, owner_id="user-a")

    # 合法顺序：保持 step_1 → step_2 → step_3 的拓扑序（原序）应成功。
    good = _action(plan_id, plan.revision, "step_reorder", "step", None,
                   {"ordered_ids": ["step_1", "step_2", "step_3"]}, "client_reorder_good")
    updated, _ = planner.apply_action(plan_id, good, owner_id="user-a")
    assert [step.id for step in updated.steps] == ["step_1", "step_2", "step_3"]


def test_add_step_with_unknown_dependency_is_rejected() -> None:
    planner, plan_id, plan = _drafting_plan()
    action = _action(plan_id, plan.revision, "step_add", "step", None,
                     {"step": {"id": "step_4", "title": "Monitor", "depends_on": ["ghost"]}},
                     "client_addstep_bad")
    with pytest.raises(PlanningPolicyError, match="unknown step"):
        planner.apply_action(plan_id, action, owner_id="user-a")


def test_split_step_reparents_dependents() -> None:
    planner, plan_id, plan = _drafting_plan()
    action = _action(plan_id, plan.revision, "step_split", "step", "step_2",
                     {"children": [
                         {"id": "step_2a", "title": "Build API core", "depends_on": ["step_1"]},
                         {"id": "step_2b", "title": "Add auth", "depends_on": ["step_2a"]},
                     ]},
                     "client_split_01")
    updated, _ = planner.apply_action(plan_id, action, owner_id="user-a")
    ids = [step.id for step in updated.steps]
    assert "step_2" not in ids
    assert "step_2a" in ids and "step_2b" in ids
    # 原本依赖 step_2 的 step_3 改指向第一个子步骤。
    step3 = next(step for step in updated.steps if step.id == "step_3")
    assert "step_2a" in step3.depends_on


def test_merge_steps_combines_dependents() -> None:
    planner, plan_id, plan = _drafting_plan()
    action = _action(plan_id, plan.revision, "step_merge", "step", None,
                     {"step_ids": ["step_1", "step_2"],
                      "merged": {"id": "step_12", "title": "DB + API", "depends_on": []}},
                     "client_merge_01")
    updated, _ = planner.apply_action(plan_id, action, owner_id="user-a")
    ids = [step.id for step in updated.steps]
    assert "step_12" in ids and "step_1" not in ids and "step_2" not in ids
    step3 = next(step for step in updated.steps if step.id == "step_3")
    assert "step_12" in step3.depends_on


# ---------------------------------------------------------------------------
# M6：版本 diff 与回滚
# ---------------------------------------------------------------------------

def test_rollback_restores_prior_snapshot() -> None:
    planner, plan_id, plan = _drafting_plan()
    revision_before = plan.revision
    edit = _action(plan_id, plan.revision, "assumption_edit", "assumption", "asm_0",
                   {"text": "Use MySQL"}, "client_edit_rollback")
    updated, _ = planner.apply_action(plan_id, edit, owner_id="user-a")
    assert updated.revision == revision_before + 1

    rollback = InteractionAction.model_validate({
        "plan_id": plan_id,
        "plan_revision": updated.revision,
        "component_type": "plan_diff",
        "action_type": "revision_rollback",
        "target_type": "revision",
        "target_id": str(revision_before),
        "idempotency_key": "client_rollback_1",
    })
    restored, affected = planner.apply_action(plan_id, rollback, owner_id="user-a")
    assert affected["rolled_back_to"] == revision_before
    assert restored.revision == updated.revision + 1
    assert restored.assumptions == ["Use PostgreSQL", "Deploy on single server"]
    assert restored.status == PlanStatus.DRAFTING


def test_rollback_to_current_revision_is_rejected() -> None:
    planner, plan_id, plan = _drafting_plan()
    rollback = InteractionAction.model_validate({
        "plan_id": plan_id,
        "plan_revision": plan.revision,
        "component_type": "plan_diff",
        "action_type": "revision_rollback",
        "target_type": "revision",
        "target_id": str(plan.revision),
        "idempotency_key": "client_rollback_2",
    })
    with pytest.raises(PlanningConflict, match="current or a future"):
        planner.apply_action(plan_id, rollback, owner_id="user-a")


def test_compute_plan_diff_reports_changes() -> None:
    planner, plan_id, plan = _drafting_plan()
    edit = _action(plan_id, plan.revision, "assumption_edit", "assumption", "asm_0",
                   {"text": "Use MySQL"}, "client_edit_diff")
    updated, _ = planner.apply_action(plan_id, edit, owner_id="user-a")
    diff = revisions.compute_plan_diff(plan, updated)
    assert diff["assumptions"]["before"] == ["Use PostgreSQL", "Deploy on single server"]
    assert diff["assumptions"]["after"] == ["Use MySQL", "Deploy on single server"]


# ---------------------------------------------------------------------------
# M5：交互编排器
# ---------------------------------------------------------------------------

def test_orchestrator_accepts_valid_ask_user() -> None:
    from app.planning.contracts import InteractionOption, InteractionType
    from app.planning.runtime import InteractionOrchestrator

    planner, plan_id, plan = _drafting_plan()
    proposal = DecisionProposal(
        protocol_version="1.0",
        action=DecisionAction.ASK_USER,
        goal="Build a blog",
        interaction={
            "type": "single_select",
            "prompt": "选择技术栈",
            "options": [{"id": "react", "label": "React"}, {"id": "vue", "label": "Vue"}],
        },
    )
    verdict = InteractionOrchestrator().evaluate(proposal, plan)
    assert verdict["valid"] is True
    assert verdict["component"] == "single_select"


def test_orchestrator_rejects_too_many_assumptions() -> None:
    from app.planning.runtime import InteractionOrchestrator

    planner, plan_id, plan = _drafting_plan()
    proposal = DecisionProposal(
        protocol_version="1.0",
        action=DecisionAction.UPDATE_PLAN,
        goal="Build a blog",
        assumptions=[f"assumption {i}" for i in range(21)],
    )
    with pytest.raises(PlanningPolicyError, match="too many assumptions"):
        InteractionOrchestrator().enforce(proposal, plan)
