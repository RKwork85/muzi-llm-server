"""Ask 运行元数据的真实数据库事务。不调用模型。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text

from app.authorization.application.chat_run_detail import load_chat_run_detail
from app.chat.ask_runs import AskRunDecision, open_ask_run, seal_ask_run
from app.core.config import Settings, get_settings
from app.core.database.redis import close_redis
from app.core.database.session import dispose_engine, get_session_factory, init_engine
from app.models.ask_run import AskRun
from app.models.metering import LlmUsageRecord
from app.models.planning import PersistentPlanningEvent
from app.models.rbac import UsageRecord
from app.models.tables import ConversationSession, User
from app.planning.contracts import PlanningEventType


def _identity() -> tuple[str, str]:
    token = uuid.uuid4().hex[:12]
    return f"u{token}@example.com", f"u{token}"


async def _user_and_session(factory) -> tuple[uuid.UUID, uuid.UUID]:
    email, username = _identity()
    async with factory() as session:
        user = User(email=email, username=username, display_name="ask-run")
        session.add(user)
        await session.flush()
        conversation = ConversationSession(user_id=user.id, title="ask", mode="chat", status="active")
        session.add(conversation)
        await session.commit()
        return user.id, conversation.id


async def _cleanup(user_id: uuid.UUID) -> None:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("DELETE FROM sessions WHERE user_id = :id"), {"id": user_id})
        await session.execute(text("DELETE FROM llm_usage_records WHERE user_id = :id"), {"id": user_id})
        await session.execute(text("DELETE FROM usage_records WHERE user_id = :id"), {"id": user_id})
        await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        await session.commit()


async def _count_runs(factory, owner_id: uuid.UUID) -> int:
    async with factory() as session:
        value = await session.scalar(select(func.count()).select_from(AskRun).where(AskRun.owner_id == owner_id))
    return int(value or 0)


@pytest.fixture
async def database():
    get_settings.cache_clear()
    loaded = Settings()
    init_engine(loaded)
    try:
        yield loaded
    finally:
        await close_redis()
        await dispose_engine()


@pytest.fixture
async def ask_owner(database):
    del database
    factory = get_session_factory()
    owner_id, conversation_id = await _user_and_session(factory)
    try:
        yield factory, owner_id, conversation_id
    finally:
        await _cleanup(owner_id)


async def test_two_rounds_and_two_conversations_stay_distinct(ask_owner) -> None:
    factory, owner_id, conversation_id = ask_owner
    _other_owner, other_conversation = await _user_and_session(factory)
    try:
        async with factory() as session:
            first = await open_ask_run(
                session,
                owner_id=owner_id,
                conversation_id=conversation_id,
                external_id="client-round-1",
                external_id_type="client_run_id",
                fingerprint="a" * 64,
            )
            second = await open_ask_run(
                session,
                owner_id=owner_id,
                conversation_id=conversation_id,
                external_id="client-round-2",
                external_id_type="client_run_id",
                fingerprint="b" * 64,
            )
            other = await open_ask_run(
                session,
                owner_id=_other_owner,
                conversation_id=other_conversation,
                external_id="client-round-1",
                external_id_type="client_run_id",
                fingerprint="a" * 64,
            )
            await session.commit()
        assert first.decision is AskRunDecision.CREATED
        assert second.decision is AskRunDecision.CREATED
        assert first.run_id != second.run_id
        assert other.run_id != first.run_id
        assert await _count_runs(factory, owner_id) == 2
    finally:
        await _cleanup(_other_owner)


async def test_duplicate_client_id_conflicts_and_same_request_replays(ask_owner) -> None:
    factory, owner_id, conversation_id = ask_owner
    async with factory() as session:
        created = await open_ask_run(
            session,
            owner_id=owner_id,
            conversation_id=conversation_id,
            external_id="client-same",
            external_id_type="client_run_id",
            fingerprint="c" * 64,
        )
        conflict = await open_ask_run(
            session,
            owner_id=owner_id,
            conversation_id=conversation_id,
            external_id="client-same",
            external_id_type="client_run_id",
            fingerprint="d" * 64,
        )
        busy = await open_ask_run(
            session,
            owner_id=owner_id,
            conversation_id=conversation_id,
            external_id="client-same",
            external_id_type="client_run_id",
            fingerprint="c" * 64,
        )
        assert created.run_id is not None
        assert await seal_ask_run(session, created.run_id, owner_id, "completed", "terminal_event")
        replay = await open_ask_run(
            session,
            owner_id=owner_id,
            conversation_id=conversation_id,
            external_id="client-same",
            external_id_type="client_run_id",
            fingerprint="c" * 64,
        )
        await session.commit()
    assert conflict.decision is AskRunDecision.CONFLICT
    assert conflict.run_id is None
    assert busy.decision is AskRunDecision.IN_PROGRESS
    assert replay.decision is AskRunDecision.REPLAY
    assert replay.run_id == created.run_id
    assert await _count_runs(factory, owner_id) == 1


async def test_terminal_commit_race_and_rollback(ask_owner) -> None:
    factory, owner_id, conversation_id = ask_owner
    async with factory() as session:
        opened = await open_ask_run(
            session,
            owner_id=owner_id,
            conversation_id=conversation_id,
            external_id="client-terminal",
            external_id_type="client_run_id",
            fingerprint="e" * 64,
        )
        await session.commit()
        assert opened.run_id is not None
        run_id = opened.run_id
    async with factory() as first, factory() as second:
        assert await seal_ask_run(first, run_id, owner_id, "completed", "terminal_event")
        await first.commit()
        assert not await seal_ask_run(second, run_id, owner_id, "failed", "terminal_event")
        await second.commit()
    async with factory() as session:
        row = await session.get(AskRun, run_id)
        assert row is not None
        assert row.status == "completed"
        assert not await seal_ask_run(session, run_id, owner_id, "running", "late")
        await session.rollback()
    async with factory() as session:
        row = await session.get(AskRun, run_id)
        assert row is not None and row.status == "completed"


async def test_uncommitted_terminal_is_not_visible(ask_owner) -> None:
    factory, owner_id, conversation_id = ask_owner
    async with factory() as session:
        opened = await open_ask_run(
            session,
            owner_id=owner_id,
            conversation_id=conversation_id,
            external_id="client-rollback",
            external_id_type="client_run_id",
            fingerprint="f" * 64,
        )
        await session.commit()
        assert opened.run_id is not None
        assert await seal_ask_run(session, opened.run_id, owner_id, "failed", "terminal_event")
        await session.rollback()
    async with factory() as session:
        row = await session.get(AskRun, opened.run_id)
        assert row is not None
        assert row.status == "running"


async def test_detail_uses_server_id_and_does_not_bill(ask_owner) -> None:
    factory, owner_id, conversation_id = ask_owner
    other_id, _other_conversation = await _user_and_session(factory)
    try:
        async with factory() as session:
            first = await open_ask_run(
                session,
                owner_id=owner_id,
                conversation_id=conversation_id,
                external_id="client-usage-1",
                external_id_type="client_run_id",
                fingerprint="1" * 64,
            )
            second = await open_ask_run(
                session,
                owner_id=owner_id,
                conversation_id=conversation_id,
                external_id="client-usage-2",
                external_id_type="client_run_id",
                fingerprint="2" * 64,
            )
            assert first.run_id is not None and second.run_id is not None
            session.add(
                PersistentPlanningEvent(
                    event_id=f"evt_{uuid.uuid4().hex}",
                    conversation_id=conversation_id,
                    sequence=1,
                    event_type=PlanningEventType.CHAT_RUN_STARTED.value,
                    payload={"run_id": str(first.run_id)},
                    occurred_at=func.now(),
                )
            )
            session.add(
                LlmUsageRecord(
                    user_id=owner_id,
                    conversation_id=str(conversation_id),
                    settlement_key=f"{first.run_id}:1",
                    provider="deepseek",
                    model="deepseek-flash",
                    runtime_mode="ask",
                    input_tokens=3,
                    output_tokens=4,
                    total_tokens=7,
                    usage_source="chat",
                    status="completed",
                )
            )
            session.add(
                LlmUsageRecord(
                    user_id=owner_id,
                    conversation_id=str(conversation_id),
                    settlement_key=f"{second.run_id}:1",
                    provider="deepseek",
                    model="deepseek-flash",
                    runtime_mode="ask",
                    input_tokens=100,
                    output_tokens=100,
                    total_tokens=200,
                    usage_source="chat",
                    status="completed",
                )
            )
            session.add(
                UsageRecord(
                    user_id=owner_id,
                    metric="tokens",
                    amount=7,
                    source="chat",
                    settlement_key=f"{first.run_id}:1",
                    result="settled",
                    context_json={"conversation_id": str(conversation_id)},
                )
            )
            await session.commit()
            run_id = str(first.run_id)
        async with factory() as session:
            before = await session.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.user_id == owner_id))
            detail = await load_chat_run_detail(session, owner_id, run_id)
            again = await load_chat_run_detail(session, owner_id, run_id)
            hidden = await load_chat_run_detail(session, other_id, run_id)
            after = await session.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.user_id == owner_id))
        assert detail is not None and again is not None
        assert detail.status == "unknown"
        assert detail.usage.total_tokens == 7
        assert again.usage.total_tokens == 7
        assert again.status == "unknown"
        assert hidden is None
        assert before == after
    finally:
        await _cleanup(other_id)


async def test_legacy_run_without_metadata_stays_readable_and_duplicates_are_not_picked(ask_owner) -> None:
    factory, owner_id, conversation_id = ask_owner
    _other, second_conversation = await _user_and_session(factory)
    # 旧记录属于同一个用户的另一个会话。这里复用新建用户会改变归属，所以把第二会话挂到原用户。
    await _cleanup(_other)
    async with factory() as session:
        extra = ConversationSession(user_id=owner_id, title="legacy", mode="chat", status="active")
        session.add(extra)
        await session.flush()
        second_conversation = extra.id
        session.add(
            PersistentPlanningEvent(
                event_id=f"evt_{uuid.uuid4().hex}",
                conversation_id=conversation_id,
                sequence=1,
                event_type="chat.run.started",
                payload={"run_id": "legacy-run-0001"},
                occurred_at=func.now(),
            )
        )
        session.add(
            PersistentPlanningEvent(
                event_id=f"evt_{uuid.uuid4().hex}",
                conversation_id=conversation_id,
                sequence=2,
                event_type="chat.done",
                payload={"run_id": "legacy-run-0001"},
                occurred_at=func.now(),
            )
        )
        session.add(
            PersistentPlanningEvent(
                event_id=f"evt_{uuid.uuid4().hex}",
                conversation_id=second_conversation,
                sequence=1,
                event_type="chat.run.started",
                payload={"run_id": "legacy-shared-01"},
                occurred_at=func.now(),
            )
        )
        session.add(
            PersistentPlanningEvent(
                event_id=f"evt_{uuid.uuid4().hex}",
                conversation_id=conversation_id,
                sequence=3,
                event_type="chat.run.started",
                payload={"run_id": "legacy-shared-01"},
                occurred_at=func.now(),
            )
        )
        await session.commit()
    async with factory() as session:
        legacy = await load_chat_run_detail(session, owner_id, "legacy-run-0001")
        ambiguous = await load_chat_run_detail(session, owner_id, "legacy-shared-01")
    assert legacy is not None
    assert legacy.status == "completed"
    assert legacy.conversation_id == str(conversation_id)
    assert ambiguous is None
