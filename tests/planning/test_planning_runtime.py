from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.planning.contracts import InteractionSubmission, PlanStatus
from app.planning.runtime import (
    DecisionAction,
    DecisionProposal,
    PlanningConflict,
    PlanningDecisionEngine,
    PlanningNotFound,
    PlanningOrchestrator,
    PlanningPolicyError,
)
from app.providers.base import CompletionResult, ProviderChatRequest, ToolCall
from app.tools.registry import build_default_registry


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


def _pending_interaction_id(planner: PlanningOrchestrator, plan_id: str) -> str:
    return str(planner.events_after(plan_id, owner_id="user-a")[-1].payload["interaction_id"])


def test_planner_asks_one_typed_question_and_binds_it_to_server_plan_revision() -> None:
    planner = PlanningOrchestrator()
    plan = planner.create_plan(owner_id="user-a", conversation_id="conv-a", goal="Design permissions")
    updated = planner.apply_proposal(
        plan.plan_id,
        _ask_proposal(),
        owner_id="user-a",
        expected_revision=plan.revision,
    )
    events = planner.events_after(plan.plan_id, owner_id="user-a")
    interaction_id = _pending_interaction_id(planner, plan.plan_id)
    assert updated.status == PlanStatus.CLARIFYING
    assert updated.revision == 1
    assert events[-1].payload == {"interaction_id": interaction_id, "plan_revision": 1}
    interaction = planner._plans[plan.plan_id].interactions[interaction_id]
    assert interaction.plan_id == plan.plan_id
    assert interaction.plan_revision == updated.revision
    assert planner.get_plan(plan.plan_id, owner_id="user-a") == updated


def test_submission_is_validated_idempotent_and_conflicts_on_stale_revision() -> None:
    planner = PlanningOrchestrator()
    plan = planner.create_plan(owner_id="user-a", conversation_id="conv-a", goal="Design permissions")
    pending = planner.apply_proposal(
        plan.plan_id, _ask_proposal(), owner_id="user-a", expected_revision=plan.revision
    )
    interaction_id = _pending_interaction_id(planner, plan.plan_id)
    submission = InteractionSubmission(
        interaction_id=interaction_id,
        expected_plan_revision=pending.revision,
        client_submission_id="client_submit_001",
        value="rbac",
    )
    updated = planner.submit_interaction(plan.plan_id, submission, owner_id="user-a")
    assert updated.revision == pending.revision + 1
    decision = next(item for item in updated.decisions if item.key == "permission_model")
    assert decision.source == "user"
    assert decision.value == "rbac"
    assert planner.submit_interaction(plan.plan_id, submission, owner_id="user-a") == updated

    stale = submission.model_copy(update={"client_submission_id": "client_submit_002"})
    with pytest.raises(PlanningConflict, match="revision conflict"):
        planner.submit_interaction(plan.plan_id, stale, owner_id="user-a")


def test_submission_rejects_values_outside_the_server_provided_options() -> None:
    planner = PlanningOrchestrator()
    plan = planner.create_plan(owner_id="user-a", conversation_id="conv-a", goal="Design permissions")
    pending = planner.apply_proposal(
        plan.plan_id, _ask_proposal(), owner_id="user-a", expected_revision=plan.revision
    )
    interaction_id = _pending_interaction_id(planner, plan.plan_id)
    with pytest.raises(PlanningPolicyError, match="offered options"):
        planner.submit_interaction(
            plan.plan_id,
            InteractionSubmission(
                interaction_id=interaction_id,
                expected_plan_revision=pending.revision,
                client_submission_id="client_submit_002",
                value="shell_command",
            ),
            owner_id="user-a",
        )


def test_skip_is_a_separate_recorded_action() -> None:
    planner = PlanningOrchestrator()
    plan = planner.create_plan(owner_id="user-a", conversation_id="conv-a", goal="Design permissions")
    pending = planner.apply_proposal(
        plan.plan_id, _ask_proposal(), owner_id="user-a", expected_revision=plan.revision
    )
    interaction_id = _pending_interaction_id(planner, plan.plan_id)
    updated = planner.skip_interaction(
        plan.plan_id, interaction_id, owner_id="user-a", expected_revision=pending.revision
    )
    assert updated.status == PlanStatus.DRAFTING
    assert planner.events_after(plan.plan_id, owner_id="user-a")[-2].type.value == "interaction.skipped"


def test_approval_mode_switch_and_execution_authorization_are_three_steps() -> None:
    planner = PlanningOrchestrator()
    plan = planner.create_plan(owner_id="user-a", conversation_id="conv-a", goal="Design permissions")
    drafting = planner.apply_proposal(
        plan.plan_id,
        DecisionProposal(
            protocol_version="1.0",
            action=DecisionAction.UPDATE_PLAN,
            goal="Design permissions",
            steps=[{"id": "step_1", "title": "Define roles"}],
        ),
        owner_id="user-a",
        expected_revision=plan.revision,
    )
    review = planner.apply_proposal(
        plan.plan_id,
        DecisionProposal(
            protocol_version="1.0",
            action=DecisionAction.REQUEST_APPROVAL,
            goal="Design permissions",
            steps=[{"id": "step_1", "title": "Define roles"}],
        ),
        owner_id="user-a",
        expected_revision=drafting.revision,
    )
    approved = planner.approve(plan.plan_id, owner_id="user-a", expected_revision=review.revision)
    assert approved.status == PlanStatus.APPROVED
    with pytest.raises(PlanningConflict, match="switching mode does not authorize"):
        planner.authorize_execution(
            plan.plan_id,
            owner_id="user-a",
            approved_revision=approved.revision,
            expected_revision=approved.revision,
            authorization_id="auth_12345",
        )
    ready = planner.switch_mode(
        plan.plan_id, owner_id="user-a", expected_revision=approved.revision, target_mode="agent"
    )
    assert ready.status == PlanStatus.READY_FOR_HANDOFF
    running = planner.authorize_execution(
        plan.plan_id,
        owner_id="user-a",
        approved_revision=approved.revision,
        expected_revision=ready.revision,
        authorization_id="auth_12345",
    )
    assert running.status == PlanStatus.EXECUTING


def test_plan_tools_fail_closed_for_write_capabilities_and_cross_owner_reads() -> None:
    planner = PlanningOrchestrator()
    registry = build_default_registry()
    assert planner.validate_plan_tools(registry, ["calculator"]) == ("calculator",)
    with pytest.raises(PlanningPolicyError, match="not allowed"):
        planner.validate_plan_tools(registry, ["write_text_file"])
    plan = planner.create_plan(owner_id="user-a", conversation_id="conv-a", goal="Design permissions")
    with pytest.raises(PlanningNotFound):
        planner.get_plan(plan.plan_id, owner_id="user-b")


class _FakeProvider:
    def __init__(self, content: str, tool_calls: list[ToolCall] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []
        self.request: ProviderChatRequest | None = None

    async def create_chat_completion(self, request: ProviderChatRequest) -> CompletionResult:
        self.request = request
        return CompletionResult(
            id="completion_1",
            model=request.model,
            content=self.content,
            reasoning_content=None,
            tool_calls=self.tool_calls,
            finish_reason="stop",
        )


@pytest.mark.asyncio
async def test_decision_engine_requests_json_and_validates_model_proposal() -> None:
    content = json.dumps(
        {
            "protocol_version": "1.0",
            "action": "respond",
            "response": "I can use a safe default and disclose it.",
        }
    )
    provider = _FakeProvider(content)
    planner = PlanningOrchestrator()
    plan = planner.create_plan(owner_id="user-a", conversation_id="conv-a", goal="Plan a system")
    proposal = await PlanningDecisionEngine(provider, model="test-model").propose(
        goal="Plan a system", user_message="Start", plan=plan
    )
    assert proposal.action == DecisionAction.RESPOND
    assert provider.request is not None
    assert provider.request.response_format == {"type": "json_object"}
    assert provider.request.tools is None
    system_prompt = provider.request.messages[0]["content"]
    assert '"$defs"' in system_prompt
    assert "Do NOT include protocol_version, interaction_id, plan_id, plan_revision, status, or plan_review" in system_prompt


@pytest.mark.asyncio
async def test_decision_engine_accepts_interaction_without_server_owned_fields() -> None:
    content = json.dumps(
        {
            "protocol_version": "1.0",
            "action": "ask_user",
            "response": "我先确认一下你的偏好。",
            "interaction": {
                "type": "single_select",
                "prompt": "你希望每天安排多长时间？",
                "options": [
                    {"id": "short", "label": "1 小时"},
                    {"id": "long", "label": "2 小时"},
                ],
            },
        },
        ensure_ascii=False,
    )
    provider = _FakeProvider(content)
    plan = PlanningOrchestrator().create_plan(
        owner_id="user-a", conversation_id="conv-a", goal="学习一个新主题"
    )
    proposal = await PlanningDecisionEngine(provider, model="test-model").propose(
        goal=plan.goal, user_message="开始规划", plan=plan
    )
    assert proposal.interaction is not None
    assert proposal.interaction.prompt == "你希望每天安排多长时间？"
    assert not hasattr(proposal.interaction, "interaction_id")


@pytest.mark.asyncio
async def test_decision_engine_rejects_tool_calls_and_invalid_json() -> None:
    planner = PlanningOrchestrator()
    plan = planner.create_plan(owner_id="user-a", conversation_id="conv-a", goal="Plan a system")
    with pytest.raises(PlanningPolicyError, match="cannot request tool execution"):
        await PlanningDecisionEngine(
            _FakeProvider("{}", [ToolCall(id="call_1", name="write_text_file", arguments="{}")]),
            model="test-model",
        ).propose(goal="Plan", user_message="Write a file", plan=plan)
    with pytest.raises(PlanningPolicyError, match="invalid proposal"):
        await PlanningDecisionEngine(_FakeProvider("not json"), model="test-model").propose(
            goal="Plan", user_message="Start", plan=plan
        )


def test_proposed_interaction_rejects_unimplemented_component() -> None:
    # M2：模型提案层仅白名单放行已实现渲染的组件，assumption_editor 等留待 M3/M4。
    from app.planning.runtime import ProposedInteraction

    with pytest.raises(ValidationError):
        ProposedInteraction.model_validate({"type": "assumption_editor", "prompt": "请编辑这条假设"})

