from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from app.authorization.application.chat_run_detail import (
    assemble_chat_run,
    chat_run_event_statement,
)


def _event(conversation_id, event_type, occurred_at, sequence, run_id="run-aaaa"):
    return SimpleNamespace(
        conversation_id=conversation_id,
        event_type=event_type,
        occurred_at=occurred_at,
        sequence=sequence,
        payload={"run_id": run_id},
    )


def _llm(user_id, conversation_id, key, **tokens):
    return SimpleNamespace(
        user_id=user_id,
        conversation_id=str(conversation_id),
        settlement_key=key,
        model=tokens.get("model", "deepseek-flash"),
        runtime_mode=tokens.get("runtime_mode", "ask"),
        status="completed",
        input_tokens=tokens.get("input_tokens"),
        output_tokens=tokens.get("output_tokens"),
        reasoning_tokens=tokens.get("reasoning_tokens"),
        total_tokens=tokens.get("total_tokens"),
        created_at=tokens.get("created_at", datetime(2026, 11, 13, tzinfo=timezone.utc)),
    )


def _usage(user_id, conversation_id, key, amount, result="settled"):
    return SimpleNamespace(
        user_id=user_id,
        settlement_key=key,
        amount=amount,
        result=result,
        context_json={
            "conversation_id": str(conversation_id),
            "model": "deepseek-flash",
            "prompt_tokens": amount,
            "completion_tokens": 1,
        },
    )


def test_completed_run_sums_one_ask_and_ignores_another_users_usage() -> None:
    owner = uuid.uuid4()
    other = uuid.uuid4()
    conversation_id = uuid.uuid4()
    start = datetime(2026, 11, 13, 1, 0, tzinfo=timezone.utc)
    done = start + timedelta(seconds=2)
    detail = assemble_chat_run(
        "run-aaaa",
        owner,
        [
            _event(conversation_id, "chat.run.started", start, 1),
            _event(conversation_id, "chat.done", done, 4),
        ],
        [
            _llm(owner, conversation_id, "run-aaaa:1", input_tokens=3, output_tokens=4, reasoning_tokens=None, total_tokens=7),
            _llm(owner, conversation_id, "run-aaaa:2", input_tokens=5, output_tokens=6, reasoning_tokens=None, total_tokens=11, created_at=start),
            _llm(other, conversation_id, "run-aaaa:9", input_tokens=100, output_tokens=100, reasoning_tokens=1, total_tokens=200),
        ],
        [
            _usage(owner, conversation_id, "run-aaaa:1", 7),
            _usage(owner, conversation_id, "run-aaaa:1", 7),
            _usage(owner, conversation_id, "run-aaaa:2", 11),
            _usage(other, conversation_id, "run-aaaa:9", 200),
        ],
        [
            SimpleNamespace(
                user_id=owner,
                conversation_id=str(conversation_id),
                task_id="run-aaaa",
                tool_name="calculator",
                status="completed",
                duration_ms=12,
                external_cost=None,
                arguments='{"expression":"1+1"}',
            )
        ],
    )

    assert detail is not None
    assert detail.status == "completed"
    assert detail.status_reason == "terminal_event"
    assert detail.status_source == "plan_events"
    assert detail.conversation_id == str(conversation_id)
    assert detail.model == "deepseek-flash"
    assert detail.mode == "ask"
    assert detail.duration_ms == 2000
    assert detail.usage.input_tokens == 8
    assert detail.usage.output_tokens == 10
    assert detail.usage.total_tokens == 18
    assert detail.usage.reasoning_tokens is None
    assert detail.usage.quota_consumed == 18
    assert detail.usage.calls is not None and len(detail.usage.calls) == 2
    assert detail.usage.tool_usage is not None
    assert detail.usage.tool_usage[0].tool_name == "calculator"
    assert "arguments" not in detail.usage.tool_usage[0].model_dump()


def test_failed_run_is_not_reported_completed_and_rounds_stay_separate() -> None:
    owner = uuid.uuid4()
    first = uuid.uuid4()
    second = uuid.uuid4()
    moment = datetime(2026, 11, 13, 2, 0, tzinfo=timezone.utc)
    failed = assemble_chat_run(
        "run-fail1",
        owner,
        [
            _event(first, "chat.run.started", moment, 1, "run-fail1"),
            _event(first, "chat.error", moment, 2, "run-fail1"),
        ],
        [],
        [_usage(owner, first, "run-fail1:1", 4)],
        [],
    )
    second_run = assemble_chat_run(
        "run-ok123",
        owner,
        [_event(second, "chat.run.started", moment, 3, "run-ok123"), _event(second, "chat.done", moment, 4, "run-ok123")],
        [],
        [_usage(owner, second, "run-ok123:1", 9)],
        [],
    )

    assert failed is not None and failed.status == "failed"
    assert failed.status_reason == "terminal_event"
    assert failed.usage.total_tokens == 4
    assert second_run is not None and second_run.status == "completed"
    assert second_run.usage.total_tokens == 9
    assert failed.conversation_id != second_run.conversation_id


def test_missing_usage_stays_null() -> None:
    owner = uuid.uuid4()
    conversation_id = uuid.uuid4()
    moment = datetime(2026, 11, 13, 3, 0, tzinfo=timezone.utc)
    detail = assemble_chat_run(
        "run-empty",
        owner,
        [_event(conversation_id, "chat.run.started", moment, 1, "run-empty")],
        [],
        [],
        [],
    )
    assert detail is not None
    assert detail.status == "unknown"
    assert detail.status_reason == "terminal_event_missing"
    assert detail.status_source == "historical_inference"
    assert detail.usage.input_tokens is None
    assert detail.usage.total_tokens is None
    assert detail.usage.quota_consumed is None
    assert detail.usage.tool_usage is None
    assert detail.finished_at is None


def test_event_query_is_scoped_to_the_session_owner() -> None:
    owner = uuid.uuid4()
    statement = chat_run_event_statement(owner, "run-aaaa")
    sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "plan_events" in sql
    assert "sessions" in sql
    assert "sessions.user_id" in sql
    assert str(owner) in sql
    assert "run-aaaa" in sql
    assert "chat.run.started" in sql


def test_tokens_do_not_turn_a_missing_terminal_into_completed() -> None:
    owner = uuid.uuid4()
    conversation_id = uuid.uuid4()
    moment = datetime(2026, 11, 13, 4, 0, tzinfo=timezone.utc)
    detail = assemble_chat_run(
        "run-hist1",
        owner,
        [_event(conversation_id, "chat.run.started", moment, 1, "run-hist1")],
        [],
        [_usage(owner, conversation_id, "run-hist1:1", 3885)],
        [],
    )
    assert detail is not None
    assert detail.status == "unknown"
    assert detail.status != "cancelled"
    assert detail.usage.total_tokens == 3885
    assert detail.usage.quota_consumed == 3885
    again = assemble_chat_run(
        "run-hist1",
        owner,
        [_event(conversation_id, "chat.run.started", moment, 1, "run-hist1")],
        [],
        [_usage(owner, conversation_id, "run-hist1:1", 3885)],
        [],
    )
    assert again is not None and again.usage.quota_consumed == detail.usage.quota_consumed


def test_live_registry_flag_is_running_until_a_terminal_arrives() -> None:
    owner = uuid.uuid4()
    conversation_id = uuid.uuid4()
    moment = datetime(2026, 11, 13, 5, 0, tzinfo=timezone.utc)
    started = [_event(conversation_id, "chat.run.started", moment, 1, "run-live1")]
    running = assemble_chat_run("run-live1", owner, started, [], [], [], active=True)
    idle = assemble_chat_run("run-live1", owner, started, [], [], [], active=False)
    finished = assemble_chat_run(
        "run-live1",
        owner,
        started + [_event(conversation_id, "chat.done", moment, 2, "run-live1")],
        [],
        [],
        [],
        active=True,
    )
    assert running is not None and running.status == "running"
    assert running.status_source == "run_registry"
    assert idle is not None and idle.status == "unknown"
    assert finished is not None and finished.status == "completed"
    assert finished.status_source == "plan_events"


def test_latest_terminal_wins_and_cancel_requires_an_event() -> None:
    owner = uuid.uuid4()
    conversation_id = uuid.uuid4()
    moment = datetime(2026, 11, 13, 6, 0, tzinfo=timezone.utc)
    cancelled = assemble_chat_run(
        "run-stop1",
        owner,
        [
            _event(conversation_id, "chat.run.started", moment, 1, "run-stop1"),
            _event(conversation_id, "chat.cancelled", moment, 2, "run-stop1"),
        ],
        [],
        [],
        [],
    )
    late_success = assemble_chat_run(
        "run-late1",
        owner,
        [
            _event(conversation_id, "chat.run.started", moment, 1, "run-late1"),
            _event(conversation_id, "chat.error", moment, 2, "run-late1"),
            _event(conversation_id, "chat.done", moment, 3, "run-late1"),
        ],
        [],
        [],
        [],
    )
    late_failure = assemble_chat_run(
        "run-late2",
        owner,
        [
            _event(conversation_id, "chat.run.started", moment, 1, "run-late2"),
            _event(conversation_id, "chat.done", moment, 2, "run-late2"),
            _event(conversation_id, "chat.error", moment, 4, "run-late2"),
        ],
        [],
        [],
        [],
    )
    assert cancelled is not None and cancelled.status == "cancelled"
    assert cancelled.status_reason == "terminal_event"
    assert late_success is not None and late_success.status == "completed"
    assert late_success.status_reason == "latest_terminal_event"
    assert late_failure is not None and late_failure.status == "failed"
    assert late_failure.status_reason == "latest_terminal_event"
