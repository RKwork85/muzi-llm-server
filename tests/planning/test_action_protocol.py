"""M2 — 统一交互动作协议 (InteractionAction) 与校验框架测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.planning.contracts import (
    ActionType,
    InteractionAction,
    InteractionStatus,
    InteractionType,
    TargetType,
)
from app.planning.runtime import (
    DecisionAction,
    DecisionProposal,
    PlanningConflict,
    PlanningNotFound,
    PlanningOrchestrator,
    PlanningPolicyError,
)


def _ask_proposal() -> DecisionProposal:
    return DecisionProposal.model_validate(
        {
            "protocol_version": "1.0",
            "action": "ask_user",
            "response": "Which permission model should I plan for?",
            "interaction": {
                "type": "single_select",
                "prompt": "Choose a permission model",
                "options": [
                    {"id": "rbac", "label": "RBAC"},
                    {"id": "abac", "label": "ABAC"},
                ],
                "constraints": {"decision_key": "permission_model"},
            },
        }
    )


def _pending(planner: PlanningOrchestrator) -> tuple[str, int]:
    plan = planner.create_plan(owner_id="user-a", conversation_id="conv-a", goal="Design permissions")
    updated = planner.apply_proposal(plan.plan_id, _ask_proposal(), owner_id="user-a", expected_revision=plan.revision)
    interaction_id = str(planner.events_after(plan.plan_id, owner_id="user-a")[-1].payload["interaction_id"])
    return plan.plan_id, updated.revision, interaction_id


def _submit_action(plan_id: str, revision: int, interaction_id: str, value: object = "rbac", key: str = "client_submit_001") -> InteractionAction:
    return InteractionAction.model_validate(
        {
            "plan_id": plan_id,
            "plan_revision": revision,
            "component_type": "single_select",
            "action_type": "submit",
            "target_type": "interaction",
            "interaction_id": interaction_id,
            "payload": {"value": value},
            "idempotency_key": key,
        }
    )


# ---------------------------------------------------------------------------
# 契约层：动作白名单、目标定位、组件命名、状态机扩展
# ---------------------------------------------------------------------------

def test_action_contract_requires_compatible_target() -> None:
    action = _submit_action("plan_01", 1, "int_01")
    assert action.action_type == ActionType.SUBMIT
    assert action.target_type == TargetType.INTERACTION

    # 动作与目标对象类型不匹配应被契约拒绝。
    with pytest.raises(ValidationError):
        InteractionAction.model_validate(
            {
                "plan_id": "plan_01",
                "plan_revision": 1,
                "component_type": "single_select",
                "action_type": "submit",
                "target_type": "step",
                "interaction_id": "int_01",
                "idempotency_key": "client_submit_001",
            }
        )

    # 目标为交互卡片却缺少 interaction_id 应被拒绝。
    with pytest.raises(ValidationError):
        InteractionAction.model_validate(
            {
                "plan_id": "plan_01",
                "plan_revision": 1,
                "component_type": "single_select",
                "action_type": "submit",
                "target_type": "interaction",
                "idempotency_key": "client_submit_001",
            }
        )


def test_action_contract_rejects_invalid_idempotency_key() -> None:
    with pytest.raises(ValidationError):
        _submit_action("plan_01", 1, "int_01", key="short")


def test_component_names_are_unified_and_frozen() -> None:
    # 权威名为 snake_case，且不存在 single_choice / multi_choice 别名枚举。
    assert InteractionType.SINGLE_SELECT.value == "single_select"
    assert InteractionType.MULTI_SELECT.value == "multi_select"
    for member in InteractionType:
        assert member.value not in {"single_choice", "multi_choice"}

    # M3/M4 计划编辑组件已登记白名单。
    assert InteractionType.ASSUMPTION_EDITOR.value == "assumption_editor"
    assert InteractionType.PLAN_EDITOR.value == "plan_editor"
    assert InteractionType.STEP_REORDER.value == "step_reorder"
    assert InteractionType.PLAN_DIFF.value == "plan_diff"


def test_interaction_status_machine_extended() -> None:
    assert InteractionStatus.PROCESSING.value == "processing"
    assert InteractionStatus.COMPLETED.value == "completed"
    assert InteractionStatus.FAILED.value == "failed"


# ---------------------------------------------------------------------------
# 运行时：统一动作入口 apply_action
# ---------------------------------------------------------------------------

def test_apply_action_submits_through_unified_protocol() -> None:
    planner = PlanningOrchestrator()
    plan_id, revision, interaction_id = _pending(planner)
    updated, _ = planner.apply_action(plan_id, _submit_action(plan_id, revision, interaction_id), owner_id="user-a")
    assert updated.revision == revision + 1
    decision = next(item for item in updated.decisions if item.key == "permission_model")
    assert decision.source == "user"
    assert decision.value == "rbac"


def test_apply_action_is_idempotent_on_same_key() -> None:
    planner = PlanningOrchestrator()
    plan_id, revision, interaction_id = _pending(planner)
    action = _submit_action(plan_id, revision, interaction_id)
    first, _ = planner.apply_action(plan_id, action, owner_id="user-a")
    second, _ = planner.apply_action(plan_id, action, owner_id="user-a")
    assert second == first
    assert second.revision == revision + 1

    # 同一个幂等键被用于不同动作应报冲突。
    other = _submit_action(plan_id, revision, interaction_id, value="abac", key=action.idempotency_key)
    with pytest.raises(PlanningConflict, match="idempotency key was reused"):
        planner.apply_action(plan_id, other, owner_id="user-a")


def test_apply_action_rejects_stale_revision() -> None:
    planner = PlanningOrchestrator()
    plan_id, revision, interaction_id = _pending(planner)
    planner.apply_action(plan_id, _submit_action(plan_id, revision, interaction_id, key="k00000001"), owner_id="user-a")
    # 计划已推进到 revision+1，旧版本动作应触发 CAS 冲突。
    stale = _submit_action(plan_id, revision, interaction_id, key="k00000002")
    with pytest.raises(PlanningConflict, match="revision conflict"):
        planner.apply_action(plan_id, stale, owner_id="user-a")


def test_apply_action_rejects_cross_owner() -> None:
    planner = PlanningOrchestrator()
    plan_id, revision, interaction_id = _pending(planner)
    with pytest.raises(PlanningNotFound):
        planner.apply_action(plan_id, _submit_action(plan_id, revision, interaction_id), owner_id="user-b")


def test_apply_action_rejects_value_outside_options() -> None:
    planner = PlanningOrchestrator()
    plan_id, revision, interaction_id = _pending(planner)
    with pytest.raises(PlanningPolicyError, match="offered options"):
        planner.apply_action(
            plan_id,
            _submit_action(plan_id, revision, interaction_id, value="shell_command"),
            owner_id="user-a",
        )


def test_apply_action_skips_interaction() -> None:
    planner = PlanningOrchestrator()
    plan_id, revision, interaction_id = _pending(planner)
    skip = InteractionAction.model_validate(
        {
            "plan_id": plan_id,
            "plan_revision": revision,
            "component_type": "single_select",
            "action_type": "skip",
            "target_type": "interaction",
            "interaction_id": interaction_id,
            "idempotency_key": "client_skip_0001",
        }
    )
    updated, _ = planner.apply_action(plan_id, skip, owner_id="user-a")
    assert updated.revision == revision + 1
    assert planner.events_after(plan_id, owner_id="user-a")[-2].type.value == "interaction.skipped"


def test_apply_action_rejects_unimplemented_action() -> None:
    planner = PlanningOrchestrator()
    plan_id, revision, interaction_id = _pending(planner)
    # M6 版本动作尚未实现（revision_confirm 留待 M6）。
    edit = InteractionAction.model_validate(
        {
            "plan_id": plan_id,
            "plan_revision": revision,
            "component_type": "plan_diff",
            "action_type": "revision_confirm",
            "target_type": "revision",
            "target_id": "1",
            "idempotency_key": "client_revconf_1",
        }
    )
    with pytest.raises(PlanningPolicyError, match="not implemented"):
        planner.apply_action(plan_id, edit, owner_id="user-a")
