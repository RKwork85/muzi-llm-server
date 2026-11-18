"""执行期 todo 清单：契约校验、merge 语义与内存调度器行为。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.planning.contracts import (
    PlanSnapshot,
    PlanStatus,
    PlanTodo,
    PlanTodoSet,
    PlanningEventType,
    TodoStatus,
    TodoUpdateRequest,
)
from app.planning.runtime import (
    DecisionAction,
    DecisionProposal,
    PlanningConflict,
    PlanningNotFound,
    PlanningOrchestrator,
    complete_remaining_todos,
    first_startable_todo,
    mark_todo_in_progress,
    merge_todo_items,
)

GOAL = "Design permissions"
STEPS = [
    {"id": "step_1", "title": "Define roles"},
    {"id": "step_2", "title": "Map grants"},
]


def _executing(planner: PlanningOrchestrator) -> PlanSnapshot:
    plan = planner.create_plan(owner_id="user-a", conversation_id="conv-a", goal=GOAL)
    drafting = planner.apply_proposal(
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
    approved = planner.approve(plan.plan_id, owner_id="user-a", expected_revision=review.revision)
    ready = planner.switch_mode(
        plan.plan_id, owner_id="user-a", expected_revision=approved.revision, target_mode="agent"
    )
    return planner.authorize_execution(
        plan.plan_id,
        owner_id="user-a",
        approved_revision=approved.revision,
        expected_revision=ready.revision,
        authorization_id="auth_test_1234",
    )


# ---------------------------------------------------------------------------
# 契约
# ---------------------------------------------------------------------------

def test_todo_status_has_exactly_four_states() -> None:
    assert [item.value for item in TodoStatus] == [
        "pending",
        "in_progress",
        "completed",
        "cancelled",
    ]


def test_plan_todo_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        PlanTodo(id="step_1", content="Define roles", status="done")


def test_plan_todo_rejects_unsupported_id() -> None:
    with pytest.raises(ValidationError):
        PlanTodo(id="step 1", content="Define roles")


def test_todo_set_rejects_duplicate_ids() -> None:
    with pytest.raises(ValidationError):
        PlanTodoSet(
            plan_id="plan_1",
            source_plan_revision=0,
            todos=[
                PlanTodo(id="step_1", content="a"),
                PlanTodo(id="step_1", content="b"),
            ],
            updated_at="2026-11-18T00:00:00+00:00",
        )


def test_todo_update_rejects_duplicate_ids() -> None:
    with pytest.raises(ValidationError):
        TodoUpdateRequest(
            todos=[
                PlanTodo(id="step_1", content="a"),
                PlanTodo(id="step_1", content="b"),
            ]
        )


# ---------------------------------------------------------------------------
# merge 语义（纯函数）
# ---------------------------------------------------------------------------

def _todos(*statuses: TodoStatus) -> list[PlanTodo]:
    return [
        PlanTodo(id=f"step_{index + 1}", content=f"step {index + 1}", status=status)
        for index, status in enumerate(statuses)
    ]


def test_merge_true_updates_listed_items_and_keeps_others() -> None:
    current = _todos(TodoStatus.PENDING, TodoStatus.PENDING)
    request = TodoUpdateRequest(
        todos=[PlanTodo(id="step_1", content="step 1", status=TodoStatus.IN_PROGRESS)],
        merge=True,
    )

    merged = merge_todo_items(current, request)

    assert [item.id for item in merged] == ["step_1", "step_2"]
    assert merged[0].status is TodoStatus.IN_PROGRESS
    assert merged[1].status is TodoStatus.PENDING


def test_merge_true_appends_unknown_items() -> None:
    current = _todos(TodoStatus.PENDING)
    request = TodoUpdateRequest(
        todos=[PlanTodo(id="todo_extra", content="runtime addition", status=TodoStatus.PENDING)],
        merge=True,
    )

    merged = merge_todo_items(current, request)

    assert [item.id for item in merged] == ["step_1", "todo_extra"]


def test_merge_false_cancels_missing_items_instead_of_deleting() -> None:
    current = _todos(TodoStatus.PENDING, TodoStatus.PENDING)
    request = TodoUpdateRequest(
        todos=[PlanTodo(id="step_1", content="step 1", status=TodoStatus.COMPLETED)],
        merge=False,
    )

    merged = merge_todo_items(current, request)

    assert [item.id for item in merged] == ["step_1", "step_2"]
    assert merged[0].status is TodoStatus.COMPLETED
    assert merged[1].status is TodoStatus.CANCELLED


def test_merge_false_keeps_already_cancelled_items_once() -> None:
    current = _todos(TodoStatus.CANCELLED)
    request = TodoUpdateRequest(todos=[], merge=False)

    merged = merge_todo_items(current, request)

    assert [item.id for item in merged] == ["step_1"]
    assert merged[0].status is TodoStatus.CANCELLED


# ---------------------------------------------------------------------------
# 内存调度器
# ---------------------------------------------------------------------------

def test_authorize_execution_initializes_todos_from_plan_steps() -> None:
    planner = PlanningOrchestrator()
    executing = _executing(planner)

    checklist = planner.get_todos(executing.plan_id, owner_id="user-a")

    assert checklist is not None
    assert [item.id for item in checklist.todos] == ["step_1", "step_2"]
    assert [item.content for item in checklist.todos] == ["Define roles", "Map grants"]
    assert all(item.status is TodoStatus.PENDING for item in checklist.todos)
    assert checklist.source_plan_revision == executing.revision


def test_initialize_todos_is_idempotent() -> None:
    planner = PlanningOrchestrator()
    executing = _executing(planner)

    first = planner.initialize_todos(executing.plan_id, owner_id="user-a")
    second = planner.initialize_todos(executing.plan_id, owner_id="user-a")

    assert [item.id for item in first.todos] == [item.id for item in second.todos]
    events = [
        event
        for event in planner.events_after(executing.plan_id, owner_id="user-a")
        if event.type is PlanningEventType.TODO_UPDATED
    ]
    assert len(events) == 1


def test_initialize_rejects_plan_that_is_not_executing() -> None:
    planner = PlanningOrchestrator()
    plan = planner.create_plan(owner_id="user-a", conversation_id="conv-a", goal=GOAL)

    with pytest.raises(PlanningConflict):
        planner.initialize_todos(plan.plan_id, owner_id="user-a")


def test_todo_update_does_not_bump_plan_revision() -> None:
    planner = PlanningOrchestrator()
    executing = _executing(planner)

    checklist = planner.apply_todo_update(
        executing.plan_id,
        TodoUpdateRequest(
            todos=[PlanTodo(id="step_1", content="Define roles", status=TodoStatus.IN_PROGRESS)]
        ),
        owner_id="user-a",
    )

    assert checklist.todos[0].status is TodoStatus.IN_PROGRESS
    assert planner.get_plan(executing.plan_id, owner_id="user-a").revision == executing.revision


def test_todo_update_accepts_paused_plan() -> None:
    planner = PlanningOrchestrator()
    executing = _executing(planner)
    planner.apply_todo_update(
        executing.plan_id,
        TodoUpdateRequest(todos=[], merge=True),
        owner_id="user-a",
    )

    paused = planner._revise(planner.get_plan(executing.plan_id, owner_id="user-a"), status=PlanStatus.PAUSED)
    record = planner._owned(executing.plan_id, "user-a")
    record.current = paused
    record.revisions.append(paused)

    checklist = planner.apply_todo_update(
        executing.plan_id,
        TodoUpdateRequest(
            todos=[PlanTodo(id="step_2", content="Map grants", status=TodoStatus.COMPLETED)]
        ),
        owner_id="user-a",
    )

    assert checklist.todos[1].status is TodoStatus.COMPLETED


def test_todo_update_rejects_mismatched_expected_revision() -> None:
    planner = PlanningOrchestrator()
    executing = _executing(planner)

    with pytest.raises(PlanningConflict):
        planner.apply_todo_update(
            executing.plan_id,
            TodoUpdateRequest(todos=[], merge=True, expected_plan_revision=executing.revision + 5),
            owner_id="user-a",
        )


def test_todo_update_requires_owner() -> None:
    planner = PlanningOrchestrator()
    executing = _executing(planner)

    with pytest.raises(PlanningNotFound):
        planner.apply_todo_update(
            executing.plan_id,
            TodoUpdateRequest(todos=[], merge=True),
            owner_id="user-b",
        )


def test_todo_update_emits_event_with_merge_flag() -> None:
    planner = PlanningOrchestrator()
    executing = _executing(planner)

    planner.apply_todo_update(
        executing.plan_id,
        TodoUpdateRequest(
            todos=[PlanTodo(id="step_1", content="Define roles", status=TodoStatus.COMPLETED)],
            merge=True,
        ),
        owner_id="user-a",
    )

    events = [
        event
        for event in planner.events_after(executing.plan_id, owner_id="user-a")
        if event.type is PlanningEventType.TODO_UPDATED
    ]
    assert len(events) == 2
    assert events[-1].payload["merge"] is True
    assert events[-1].payload["todos"][0]["status"] == "completed"


# ---------------------------------------------------------------------------
# 执行期自动进度推导（纯函数）
# ---------------------------------------------------------------------------

def _make_todo(todo_id: str, status: TodoStatus, depends_on: list[str] | None = None) -> PlanTodo:
    return PlanTodo(id=todo_id, content=todo_id, status=status, depends_on=depends_on or [])


def test_first_startable_returns_first_dependency_satisfied_pending() -> None:
    todos = [
        _make_todo("a", TodoStatus.PENDING),
        _make_todo("b", TodoStatus.PENDING, depends_on=["a"]),
    ]

    target = first_startable_todo(todos)

    assert target is not None and target.id == "a"


def test_first_startable_skips_blocked_dependencies() -> None:
    todos = [
        _make_todo("a", TodoStatus.PENDING),
        _make_todo("b", TodoStatus.PENDING, depends_on=["a"]),
        _make_todo("c", TodoStatus.PENDING, depends_on=["b"]),
    ]
    # a 未完成，b/c 都被依赖阻塞，仍应只点亮 a。
    target = first_startable_todo(todos)

    assert target is not None and target.id == "a"


def test_first_startable_advances_after_dependency_completed() -> None:
    todos = [
        _make_todo("a", TodoStatus.COMPLETED),
        _make_todo("b", TodoStatus.PENDING, depends_on=["a"]),
    ]

    target = first_startable_todo(todos)

    assert target is not None and target.id == "b"


def test_first_startable_returns_none_when_in_progress_exists() -> None:
    todos = [
        _make_todo("a", TodoStatus.IN_PROGRESS),
        _make_todo("b", TodoStatus.PENDING),
    ]

    assert first_startable_todo(todos) is None


def test_first_startable_returns_none_when_nothing_pending() -> None:
    todos = [
        _make_todo("a", TodoStatus.COMPLETED),
        _make_todo("b", TodoStatus.CANCELLED),
    ]

    assert first_startable_todo(todos) is None


def test_mark_todo_in_progress_changes_only_target() -> None:
    todos = [
        _make_todo("a", TodoStatus.PENDING),
        _make_todo("b", TodoStatus.PENDING),
    ]

    merged = mark_todo_in_progress(todos, "b")

    assert merged[0].status is TodoStatus.PENDING
    assert merged[1].status is TodoStatus.IN_PROGRESS


def test_complete_remaining_todos_flips_pending_and_in_progress_only() -> None:
    todos = [
        _make_todo("a", TodoStatus.PENDING),
        _make_todo("b", TodoStatus.IN_PROGRESS),
        _make_todo("c", TodoStatus.COMPLETED),
        _make_todo("d", TodoStatus.CANCELLED),
    ]

    merged = complete_remaining_todos(todos)

    assert [item.status for item in merged] == [
        TodoStatus.COMPLETED,
        TodoStatus.COMPLETED,
        TodoStatus.COMPLETED,
        TodoStatus.CANCELLED,
    ]


# ---------------------------------------------------------------------------
# 单次门（内存版）
# ---------------------------------------------------------------------------

def test_approve_and_authorize_converges_three_steps_into_one() -> None:
    planner = PlanningOrchestrator()
    plan = planner.create_plan(owner_id="user-a", conversation_id="conv-a", goal=GOAL)
    drafting = planner.apply_proposal(
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

    executing, approved_revision = planner.approve_and_authorize(
        plan.plan_id,
        owner_id="user-a",
        expected_revision=review.revision,
        authorization_id="auth_single_gate",
    )

    assert executing.status is PlanStatus.EXECUTING
    assert approved_revision == review.revision + 1

    event_types = [event.type for event in planner.events_after(plan.plan_id, owner_id="user-a")]
    assert PlanningEventType.PLAN_APPROVED in event_types
    assert PlanningEventType.HANDOFF_CREATED in event_types
    assert PlanningEventType.MODE_CHANGED in event_types
    assert PlanningEventType.RUN_STARTED in event_types

    # 清单已由 authorize_execution 触发初始化。
    checklist = planner.get_todos(plan.plan_id, owner_id="user-a")
    assert checklist is not None
    assert [item.id for item in checklist.todos] == ["step_1", "step_2"]


def test_approve_and_authorize_requires_awaiting_approval() -> None:
    planner = PlanningOrchestrator()
    plan = planner.create_plan(owner_id="user-a", conversation_id="conv-a", goal=GOAL)

    with pytest.raises(PlanningConflict):
        planner.approve_and_authorize(
            plan.plan_id,
            owner_id="user-a",
            expected_revision=plan.revision,
            authorization_id="auth_single_gate",
        )
