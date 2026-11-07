"""PostgreSQL 连接、事务、约束、连接池和时区。"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, get_settings
from app.core.database.deps import get_db_session
from app.core.database.redis import close_redis
from app.core.database.session import dispose_engine, get_engine, get_session_factory, init_engine
from app.core.security import hash_password, hash_refresh_token
from app.models.tables import (
    AgentTask,
    ConversationMessage,
    ConversationSession,
    ExecutionEvent,
    RefreshToken,
    TaskExecution,
    ToolCallRecord,
    User,
    UserCredential,
)


@pytest.fixture
async def settings():
    get_settings.cache_clear()
    loaded = Settings()
    init_engine(loaded)
    try:
        yield loaded
    finally:
        await close_redis()
        await dispose_engine()


def _identity() -> tuple[str, str]:
    token = uuid.uuid4().hex[:12]
    return f"u{token}@example.com", f"u{token}"


async def _remove_user(user_id: uuid.UUID) -> None:
    factory = get_session_factory()
    async with factory() as session:
        params = {"id": user_id}
        await session.execute(
            text(
                "DELETE FROM events WHERE task_id IN "
                "(SELECT id FROM agent_tasks WHERE user_id = :id)"
            ),
            params,
        )
        await session.execute(
            text(
                "DELETE FROM tool_calls WHERE execution_id IN ("
                "SELECT executions.id FROM executions "
                "JOIN agent_tasks ON agent_tasks.id = executions.task_id "
                "WHERE agent_tasks.user_id = :id)"
            ),
            params,
        )
        await session.execute(
            text(
                "DELETE FROM executions WHERE task_id IN "
                "(SELECT id FROM agent_tasks WHERE user_id = :id)"
            ),
            params,
        )
        await session.execute(text("DELETE FROM agent_tasks WHERE user_id = :id"), params)
        await session.execute(
            text(
                "DELETE FROM messages WHERE session_id IN "
                "(SELECT id FROM sessions WHERE user_id = :id)"
            ),
            params,
        )
        await session.execute(text("DELETE FROM sessions WHERE user_id = :id"), params)
        await session.execute(text("DELETE FROM refresh_tokens WHERE user_id = :id"), params)
        await session.execute(text("DELETE FROM user_credentials WHERE user_id = :id"), params)
        await session.execute(text("DELETE FROM users WHERE id = :id"), params)
        await session.commit()


async def test_connection_timezone_and_data_directory(settings: Settings) -> None:
    factory = get_session_factory()
    async with factory() as session:
        selected = await session.scalar(text("SELECT 1"))
        zone = await session.scalar(text("SHOW timezone"))
        directory = await session.scalar(text("SHOW data_directory"))
    assert selected == 1
    assert zone == "Asia/Shanghai"
    assert str(directory).startswith("/var/lib/postgresql/")
    assert "/postgresql/data" not in str(directory)
    assert f"Pool size: {settings.db_pool_size} " in get_engine().pool.status()


async def test_pool_checks_out_more_than_one_connection(settings: Settings) -> None:
    del settings
    factory = get_session_factory()
    async with factory() as first, factory() as second:
        await first.execute(text("SELECT 1"))
        await second.execute(text("SELECT 1"))
        assert get_engine().pool.checkedout() >= 2


async def test_insert_update_and_dependency_rollback(settings: Settings) -> None:
    del settings
    email, username = _identity()
    factory = get_session_factory()
    async with factory() as session:
        user = User(email=email, username=username, display_name="before")
        session.add(user)
        await session.commit()
        user_id = user.id

    try:
        dependency = get_db_session()
        session = await anext(dependency)
        found = await session.get(User, user_id)
        assert found is not None
        found.display_name = "after"
        with pytest.raises(StopAsyncIteration):
            await anext(dependency)

        async with factory() as session:
            stored = await session.get(User, user_id)
            assert stored is not None
            assert stored.display_name == "after"

        dependency = get_db_session()
        session = await anext(dependency)
        stored = await session.get(User, user_id)
        assert stored is not None
        stored.display_name = "rolled-back"
        await session.flush()
        with pytest.raises(RuntimeError):
            await dependency.athrow(RuntimeError("boom"))
        async with factory() as session:
            stored = await session.get(User, user_id)
            assert stored is not None
            assert stored.display_name == "after"
    finally:
        await _remove_user(user_id)


async def test_transaction_rollback_discards_insert(settings: Settings) -> None:
    del settings
    email, username = _identity()
    factory = get_session_factory()
    async with factory() as session:
        session.add(User(email=email, username=username))
        await session.flush()
        await session.rollback()
    async with factory() as session:
        found = await session.scalar(text("SELECT id FROM users WHERE email = :email"), {"email": email})
    assert found is None


async def test_unique_and_foreign_key_constraints(settings: Settings) -> None:
    del settings
    email, username = _identity()
    _, other_name = _identity()
    factory = get_session_factory()
    async with factory() as session:
        session.add(User(email=email, username=username))
        await session.commit()
        user_id = (await session.scalar(text("SELECT id FROM users WHERE email = :email"), {"email": email}))
    try:
        async with factory() as session:
            session.add(User(email=email, username=other_name))
            with pytest.raises(IntegrityError):
                await session.commit()
        async with factory() as session:
            session.add(
                ConversationMessage(session_id=uuid.uuid4(), role="user", content="orphan")
            )
            with pytest.raises(IntegrityError):
                await session.commit()
        plain = "plaintext-password"
        async with factory() as session:
            session.add(
                UserCredential(
                    user_id=user_id,
                    provider="email_password",
                    provider_uid=email,
                    password_hash=plain,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
    finally:
        await _remove_user(user_id)


async def test_graph_redaction_trigger_and_restrict_delete(settings: Settings) -> None:
    del settings
    email, username = _identity()
    factory = get_session_factory()
    now = datetime.now(timezone.utc)
    async with factory() as session:
        user = User(email=email, username=username)
        session.add(user)
        await session.flush()
        chat = ConversationSession(user_id=user.id, title="测试", mode="agent", status="active")
        session.add(chat)
        await session.flush()
        session.add(
            ConversationMessage(
                session_id=chat.id,
                role="user",
                content="你好",
                content_type="text",
            )
        )
        session.add(
            UserCredential(
                user_id=user.id,
                provider="email_password",
                provider_uid=email,
                password_hash=hash_password("correct-horse-battery"),
            )
        )
        raw_hash = hash_refresh_token("refresh-token-value-1234")
        session.add(
            RefreshToken(
                user_id=user.id,
                token_hash=raw_hash,
                expires_at=now + timedelta(days=7),
            )
        )
        task = AgentTask(
            user_id=user.id,
            session_id=chat.id,
            request_id="req-test",
            task_type="agent",
            status="completed",
            input_payload={"message": "hi", "api_key": "super-secret-value"},
            output_payload={"answer": "ok"},
        )
        session.add(task)
        await session.flush()
        execution = TaskExecution(
            task_id=task.id,
            status="succeeded",
            execution_input={"step": 1},
            execution_output={"answer": "ok"},
            started_at=now,
            finished_at=now + timedelta(seconds=2),
        )
        session.add(execution)
        await session.flush()
        session.add(
            ToolCallRecord(
                execution_id=execution.id,
                tool_name="calculator",
                arguments={"expression": "1+1", "password": "hunter22xx"},
                result={"value": "2"},
                status="succeeded",
                started_at=now,
                finished_at=now + timedelta(milliseconds=20),
            )
        )
        session.add(
            ExecutionEvent(
                task_id=task.id,
                execution_id=execution.id,
                event_type="agent.completed",
                payload={"token": "abcdefghijklmnop"},
                sequence=1,
            )
        )
        await session.commit()
        user_id = user.id

    try:
        async with factory() as session:
            stored = await session.get(User, user_id)
            assert stored is not None
            created_at = stored.created_at
        async with factory() as session:
            task = await session.scalar(text(
                "SELECT input->>'api_key' FROM agent_tasks WHERE user_id = :id"
            ), {"id": user_id})
            argument = await session.scalar(text(
                "SELECT arguments->>'password' FROM tool_calls "
                "WHERE execution_id IN (SELECT executions.id FROM executions "
                "JOIN agent_tasks ON agent_tasks.id = executions.task_id "
                "WHERE agent_tasks.user_id = :id)"
            ), {"id": user_id})
            payload = await session.scalar(text(
                "SELECT payload->>'token' FROM events WHERE task_id IN "
                "(SELECT id FROM agent_tasks WHERE user_id = :id)"
            ), {"id": user_id})
        assert task == "***"
        assert argument == "***"
        assert payload == "***"
        assert "hunter22xx" not in argument
        assert "super-secret-value" not in task

        await asyncio.sleep(0.05)
        async with factory() as session:
            await session.execute(
                text("UPDATE users SET display_name = 'later' WHERE id = :id"),
                {"id": user_id},
            )
            await session.commit()
        async with factory() as session:
            stored = await session.get(User, user_id)
            assert stored is not None
            assert stored.display_name == "later"
            assert stored.updated_at > created_at

        async with factory() as session:
            stored = await session.get(User, user_id)
            assert stored is not None
            await session.delete(stored)
            with pytest.raises(IntegrityError):
                await session.commit()
    finally:
        await _remove_user(user_id)
