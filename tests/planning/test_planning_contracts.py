from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.planning.contracts import (
    Interaction,
    InteractionStatus,
    InteractionSubmission,
    InteractionType,
    PLAN_TRANSITIONS,
    PlanApprovalRequest,
    PlanHandoff,
    PlanStatus,
    PlanningEvent,
    PlanningEventType,
    can_transition_plan,
)


def _interaction(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "protocol_version": "1.0",
        "interaction_id": "int_01",
        "plan_id": "plan_01",
        "plan_revision": 3,
        "type": "single_select",
        "prompt": "Which permission model should we use?",
        "status": "pending",
        "options": [{"id": "rbac", "label": "RBAC"}],
    }
    body.update(overrides)
    return body


def test_interaction_accepts_versioned_single_select() -> None:
    interaction = Interaction.model_validate(_interaction())
    assert interaction.protocol_version == "1.0"
    assert interaction.options[0].id == "rbac"


@pytest.mark.parametrize(
    "overrides",
    [
        {"protocol_version": "2.0"},
        {"type": "arbitrary_html"},
        {"unexpected": "field"},
        {"options": [{"id": "same", "label": "A"}, {"id": "same", "label": "B"}]},
        {"options": []},
        {"prompt": ""},
    ],
)
def test_interaction_rejects_unknown_or_invalid_payload(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Interaction.model_validate(_interaction(**overrides))


def test_non_selection_interaction_rejects_options() -> None:
    with pytest.raises(ValidationError):
        Interaction.model_validate(_interaction(type="text_input"))


def test_submission_requires_idempotency_key_and_expected_revision() -> None:
    submission = InteractionSubmission.model_validate(
        {
            "interaction_id": "int_01",
            "expected_plan_revision": 3,
            "client_submission_id": "client_123",
            "value": "rbac",
        }
    )
    assert submission.expected_plan_revision == 3
    for missing in ("expected_plan_revision", "client_submission_id"):
        body = {
            "interaction_id": "int_01",
            "expected_plan_revision": 3,
            "client_submission_id": "client_123",
            "value": "rbac",
        }
        body.pop(missing)
        with pytest.raises(ValidationError):
            InteractionSubmission.model_validate(body)


def test_event_envelope_requires_sequence_timezone_and_known_type() -> None:
    event = PlanningEvent.model_validate(
        {
            "protocol_version": "1.0",
            "event_id": "evt_01",
            "sequence": 42,
            "conversation_id": "conv_01",
            "plan_id": "plan_01",
            "run_id": None,
            "type": "interaction.required",
            "payload": {"interaction_id": "int_01"},
            "occurred_at": "2026-11-09T00:00:00Z",
        }
    )
    assert event.sequence == 42
    for overrides in ({"sequence": 0}, {"type": "made_up.event"}, {"extra": 1}):
        body = event.model_dump(mode="json")
        body.update(overrides)
        with pytest.raises(ValidationError):
            PlanningEvent.model_validate(body)
    body = event.model_dump(mode="json")
    body["occurred_at"] = "2026-11-09T00:00:00"
    with pytest.raises(ValidationError):
        PlanningEvent.model_validate(body)


def test_plan_approval_mode_switch_and_execution_authorization_are_distinct_contracts() -> None:
    approval = PlanApprovalRequest.model_validate({"expected_revision": 4})
    assert approval.expected_revision == 4
    with pytest.raises(ValidationError):
        PlanApprovalRequest.model_validate({"expected_revision": 4, "start_execution": True})

    # Distinct request types prevent treating approval or mode change as execution consent.
    from app.planning.contracts import ExecutionAuthorizationRequest, ModeSwitchRequest

    mode_switch = ModeSwitchRequest.model_validate({"expected_revision": 4, "target_mode": "agent"})
    assert mode_switch.target_mode == "agent"
    with pytest.raises(ValidationError):
        ExecutionAuthorizationRequest.model_validate(
            {"approved_plan_id": "plan_01", "approved_revision": 4, "expected_revision": 5}
        )
    authorization = ExecutionAuthorizationRequest.model_validate(
        {
            "approved_plan_id": "plan_01",
            "approved_revision": 4,
            "expected_revision": 5,
            "authorization_id": "auth_12345",
        }
    )
    assert authorization.expected_revision == 5


def test_handoff_requires_explicit_revalidation_and_steps() -> None:
    handoff = PlanHandoff.model_validate(
        {
            "protocol_version": "1.0",
            "conversation_id": "conv_01",
            "approved_plan_id": "plan_01",
            "approved_revision": 4,
            "goal": "Implement RBAC",
            "confirmed_decisions": {"model": "rbac"},
            "constraints": ["read only while planning"],
            "steps": [{"id": "step_01", "title": "Define roles"}],
            "authorization": "revalidate_at_execution",
        }
    )
    assert handoff.approved_revision == 4
    with pytest.raises(ValidationError):
        PlanHandoff.model_validate({**handoff.model_dump(), "authorization": "inherited"})
    with pytest.raises(ValidationError):
        PlanHandoff.model_validate({**handoff.model_dump(), "steps": []})


def test_plan_transition_table_has_no_direct_clarifying_to_execution() -> None:
    assert not can_transition_plan(PlanStatus.CLARIFYING, PlanStatus.EXECUTING)
    assert can_transition_plan(PlanStatus.APPROVED, PlanStatus.READY_FOR_HANDOFF)
    assert can_transition_plan(PlanStatus.READY_FOR_HANDOFF, PlanStatus.EXECUTING)
    assert PLAN_TRANSITIONS[PlanStatus.COMPLETED] == frozenset()
    assert InteractionStatus.PENDING.value == "pending"
    assert InteractionType.PLAN_REVIEW.value == "plan_review"


def test_plan_timestamps_must_be_timezone_aware_and_ordered() -> None:
    base = {
        "conversation_id": "conv_01",
        "plan_id": "plan_01",
        "revision": 1,
        "status": "drafting",
        "goal": "Create a plan",
        "created_at": datetime(2026, 11, 9, tzinfo=UTC),
        "updated_at": datetime(2026, 11, 9, tzinfo=UTC),
    }
    from app.planning.contracts import PlanSnapshot

    assert PlanSnapshot.model_validate(base).revision == 1
    with pytest.raises(ValidationError):
        PlanSnapshot.model_validate({**base, "updated_at": datetime(2026, 11, 8, tzinfo=UTC)})
    with pytest.raises(ValidationError):
        PlanSnapshot.model_validate({**base, "created_at": datetime(2026, 11, 9)})
