from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import delete

from app.core.config import load_settings
from app.core.database.session import dispose_engine, get_session_factory, init_engine
from app.models.tables import AgentTask, ConversationSession, ExecutionEvent, TaskExecution, User
from app.agents.models import AgentEventType, EventEnvelope
from app.agents.persistence import AgentRunPersistence
from app.planning.contracts import InteractionAction, InteractionSubmission
from app.planning.runtime import DecisionProposal, PlanningConflict
from app.planning.sql_store import SqlPlanningStore

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def planning_db():
    if os.getenv("RUN_PLANNING_DB_TESTS") != "1":
        pytest.skip("set RUN_PLANNING_DB_TESTS=1 for the isolated local PostgreSQL suite")
    settings = load_settings()
    if settings.runtime_env.lower() == "production":
        pytest.fail("planning database integration tests refuse production runtime")
    init_engine(settings)
    factory = get_session_factory()
    yield factory
    await dispose_engine()


async def _new_owner_and_conversation(factory):
    suffix = uuid.uuid4().hex[:12]
    async with factory() as session:
        user = User(email=f"planning-{suffix}@example.test", username=f"plan_{suffix}")
        session.add(user)
        await session.flush()
        conversation = ConversationSession(
            user_id=user.id,
            title="planning integration test",
            mode="chat",
            status="active",
            metadata_json={"workspace_mode": "plan"},
        )
        session.add(conversation)
        await session.commit()
        return user.id, conversation.id


async def _create_plan(factory, user_id, conversation_id):
    from sqlalchemy import select

    from app.models.tables import User

    async with factory() as session:
        user = await session.scalar(select(User).where(User.id == user_id))
        assert user is not None
        plan = await SqlPlanningStore(session).create_plan(
            user, str(conversation_id), "Choose a permissions model"
        )
        await session.commit()
        return user_id, plan


async def _create_pending_choice(factory, user_id, plan):
    from sqlalchemy import select

    from app.models.tables import User

    async with factory() as session:
        user = await session.scalar(select(User).where(User.id == user_id))
        assert user is not None
        proposal = DecisionProposal.model_validate(
            {
                "protocol_version": "1.0",
                "action": "ask_user",
                "interaction": {
                    "type": "single_select",
                    "prompt": "Choose one",
                    "options": [{"id": "rbac", "label": "RBAC"}],
                },
            }
        )
        updated, interaction = await SqlPlanningStore(session).apply_proposal(
            plan.plan_id, user, plan, proposal
        )
        await session.commit()
        assert interaction is not None
        return updated, interaction


async def _submit(factory, user_id, plan_id, payload):
    from sqlalchemy import select

    from app.models.tables import User

    async with factory() as session:
        user = await session.scalar(select(User).where(User.id == user_id))
        assert user is not None
        result = await SqlPlanningStore(session).submit_interaction(plan_id, user, payload)
        await session.commit()
        return result


async def _delete_fixture_rows(factory, user_id, conversation_id):
    async with factory() as session:
        from sqlalchemy import delete

        await session.execute(delete(AgentTask).where(AgentTask.session_id == conversation_id))
        await session.execute(delete(ConversationSession).where(ConversationSession.id == conversation_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


async def test_persisted_plan_survives_new_session_and_submission_replay(planning_db):
    factory = planning_db
    user_id, conversation_id = await _new_owner_and_conversation(factory)
    try:
        _, plan = await _create_plan(factory, user_id, conversation_id)
        pending, interaction = await _create_pending_choice(factory, user_id, plan)
        submission = InteractionSubmission(
            interaction_id=interaction.interaction_id,
            expected_plan_revision=pending.revision,
            client_submission_id=f"submit_{uuid.uuid4().hex}",
            value="rbac",
        )
        first = await _submit(factory, user_id, plan.plan_id, submission)
        replay = await _submit(factory, user_id, plan.plan_id, submission)
        assert replay == first

        from sqlalchemy import select

        from app.models.tables import User

        async with factory() as new_session:
            owner = await new_session.scalar(select(User).where(User.id == user_id))
            assert owner is not None
            restored_store = SqlPlanningStore(new_session)
            restored = await restored_store.get_plan(plan.plan_id, owner)
            assert restored == first
            assert await restored_store.get_pending_interaction(plan.plan_id, owner) is None
            events, next_sequence = await restored_store.conversation_events_after(
                str(conversation_id), owner, 0
            )
            assert next_sequence == len(events)
            assert any(item.type.value == "interaction.submitted" for item in events)
    finally:
        await _delete_fixture_rows(factory, user_id, conversation_id)


async def test_apply_action_submit_is_idempotent_across_sessions(planning_db):
    factory = planning_db
    user_id, conversation_id = await _new_owner_and_conversation(factory)
    try:
        _, plan = await _create_plan(factory, user_id, conversation_id)
        pending, interaction = await _create_pending_choice(factory, user_id, plan)
        action = InteractionAction.model_validate(
            {
                "plan_id": plan.plan_id,
                "plan_revision": pending.revision,
                "component_type": "single_select",
                "action_type": "submit",
                "target_type": "interaction",
                "interaction_id": interaction.interaction_id,
                "payload": {"value": "rbac"},
                "idempotency_key": "action_submit_0001",
            }
        )

        from sqlalchemy import select

        from app.models.tables import User

        async with factory() as session:
            owner = await session.scalar(select(User).where(User.id == user_id))
            assert owner is not None
            first, _ = await SqlPlanningStore(session).apply_action(plan.plan_id, owner, action)
            await session.commit()
        async with factory() as session:
            owner = await session.scalar(select(User).where(User.id == user_id))
            assert owner is not None
            replay, _ = await SqlPlanningStore(session).apply_action(plan.plan_id, owner, action)
            await session.commit()
        assert replay == first
        assert replay.revision == pending.revision + 1
    finally:
        await _delete_fixture_rows(factory, user_id, conversation_id)


async def test_concurrent_submissions_use_compare_and_swap(planning_db):
    factory = planning_db
    user_id, conversation_id = await _new_owner_and_conversation(factory)
    try:
        _, plan = await _create_plan(factory, user_id, conversation_id)
        pending, interaction = await _create_pending_choice(factory, user_id, plan)
        first = InteractionSubmission(
            interaction_id=interaction.interaction_id,
            expected_plan_revision=pending.revision,
            client_submission_id=f"submit_{uuid.uuid4().hex}",
            value="rbac",
        )
        second = first.model_copy(update={"client_submission_id": f"submit_{uuid.uuid4().hex}"})
        results = await asyncio.gather(
            _submit(factory, user_id, plan.plan_id, first),
            _submit(factory, user_id, plan.plan_id, second),
            return_exceptions=True,
        )
        assert sum(isinstance(item, PlanningConflict) for item in results) == 1
        assert sum(not isinstance(item, Exception) for item in results) == 1
    finally:
        await _delete_fixture_rows(factory, user_id, conversation_id)


async def test_handoff_agent_events_and_result_are_durable(planning_db):
    from sqlalchemy import select

    factory = planning_db
    user_id, conversation_id = await _new_owner_and_conversation(factory)
    db_task_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    task_id = db_task_id.hex
    try:
        from sqlalchemy import select

        from app.models.tables import User

        async with factory() as session:
            owner = await session.scalar(select(User).where(User.id == user_id))
            assert owner is not None
            store = SqlPlanningStore(session)
            plan = await store.create_plan(owner, str(conversation_id), "Design permissions")
            drafting, _ = await store.apply_proposal(
                plan.plan_id,
                owner,
                plan,
                DecisionProposal(
                    protocol_version="1.0",
                    action="update_plan",
                    goal=plan.goal,
                    steps=[{"id": "step_1", "title": "Define roles"}],
                ),
            )
            for_review, _ = await store.apply_proposal(
                plan.plan_id,
                owner,
                drafting,
                DecisionProposal(
                    protocol_version="1.0",
                    action="request_approval",
                    goal=plan.goal,
                    steps=[{"id": "step_1", "title": "Define roles"}],
                ),
            )
            approved = await store.approve(plan.plan_id, owner, for_review.revision)
            ready = await store.switch_mode(plan.plan_id, owner, approved.revision, "agent")
            executing = await store.authorize_execution(
                plan.plan_id,
                owner,
                approved_revision=approved.revision,
                expected_revision=ready.revision,
                authorization_id="auth_integration_123",
            )
            await session.commit()
        async with factory() as session:
            session.add(
                AgentTask(
                    id=db_task_id,
                    user_id=user_id,
                    session_id=conversation_id,
                    request_id="planning_handoff_request",
                    task_type="agent",
                    status="created",
                    input_payload={"surface": "interactive_planning_handoff", "plan_id": plan.plan_id},
                )
            )
            session.add(TaskExecution(id=execution_id, task_id=db_task_id, status="pending", execution_input={}))
            await session.commit()

        persistence = AgentRunPersistence(
            factory,
            db_task_id,
            execution_id,
            lambda: {"task_id": task_id, "request_id": "planning_handoff_request", "status": "completed", "answer": "done"},
        )
        persistence(
            EventEnvelope(
                request_id="planning_handoff_request",
                task_id=task_id,
                sequence=1,
                timestamp="2026-11-09T00:00:00+00:00",
                type=AgentEventType.STARTED,
                data={},
            )
        )
        persistence(
            EventEnvelope(
                request_id="planning_handoff_request",
                task_id=task_id,
                sequence=2,
                timestamp="2026-11-09T00:00:01+00:00",
                type=AgentEventType.COMPLETED,
                data={"status": "completed"},
            )
        )
        await persistence.drain()
        async with factory() as session:
            task = await session.get(AgentTask, db_task_id)
            execution = await session.get(TaskExecution, execution_id)
            events = list(
                await session.scalars(
                    select(ExecutionEvent).where(ExecutionEvent.task_id == db_task_id).order_by(ExecutionEvent.sequence)
                )
            )
            assert task is not None and task.status == "completed"
            assert task.output_payload["answer"] == "done"
            assert execution is not None and execution.status == "succeeded"
            assert [item.sequence for item in events] == [1, 2]
            owner = await session.scalar(select(User).where(User.id == user_id))
            assert owner is not None
            final_plan = await SqlPlanningStore(session).get_plan(plan.plan_id, owner)
            assert final_plan.revision == executing.revision + 1
            assert final_plan.status.value == "completed"
    finally:
        await _delete_fixture_rows(factory, user_id, conversation_id)


async def test_handoff_created_event_is_persisted_and_replayable(planning_db):
    """M7-T03：`handoff.created` 事件落库到 plan_events，并可通过 events_after 恢复。"""
    from sqlalchemy import select

    from app.models.tables import User

    factory = planning_db
    user_id, conversation_id = await _new_owner_and_conversation(factory)
    try:
        async with factory() as session:
            owner = await session.scalar(select(User).where(User.id == user_id))
            assert owner is not None
            store = SqlPlanningStore(session)
            plan = await store.create_plan(owner, str(conversation_id), "Design permissions")
            drafting, _ = await store.apply_proposal(
                plan.plan_id,
                owner,
                plan,
                DecisionProposal(
                    protocol_version="1.0",
                    action="update_plan",
                    goal=plan.goal,
                    steps=[{"id": "step_1", "title": "Define roles"}],
                ),
            )
            for_review, _ = await store.apply_proposal(
                plan.plan_id,
                owner,
                drafting,
                DecisionProposal(
                    protocol_version="1.0",
                    action="request_approval",
                    goal=plan.goal,
                    steps=[{"id": "step_1", "title": "Define roles"}],
                ),
            )
            approved = await store.approve(plan.plan_id, owner, for_review.revision)
            ready = await store.switch_mode(plan.plan_id, owner, approved.revision, "agent")
            assert ready.status.value == "ready_for_handoff"
            await session.commit()

        # 用全新会话恢复事件，验证 handoff.created 持久化且可重放。
        async with factory() as session:
            owner = await session.scalar(select(User).where(User.id == user_id))
            assert owner is not None
            store = SqlPlanningStore(session)
            events, next_sequence = await store.events_after(plan.plan_id, owner, 0)
            handoff_events = [item for item in events if item.type.value == "handoff.created"]
            assert len(handoff_events) == 1
            payload = handoff_events[0].payload
            assert payload["mode"] == "agent"
            assert payload["handoff"]["protocol_version"] == "1.0"
            assert payload["handoff"]["approved_plan_id"] == plan.plan_id
            assert payload["handoff"]["authorization"] == "revalidate_at_execution"
            assert payload["handoff"]["steps"] == [{"id": "step_1", "title": "Define roles"}]
            # 会话级事件恢复也能看到 handoff.created（plan 归属同一会话）。
            conversation_events, _ = await store.conversation_events_after(str(conversation_id), owner, 0)
            assert any(item.type.value == "handoff.created" for item in conversation_events)
    finally:
        await _delete_fixture_rows(factory, user_id, conversation_id)
