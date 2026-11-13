from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy.dialects import postgresql

from app.runtime.run_identity import (
    AgentEventFact,
    AgentTaskFact,
    AskEventFact,
    ExecutionFact,
    InteractionFact,
    MemoryTaskFact,
    PlanFact,
    RevisionFact,
    UsageFact,
    agent_execution_owner_statement,
    agent_task_statement,
    ask_conversation_statement,
    ask_event_statement,
    ask_settlement_belongs,
    classify_agent_task,
    classify_ask_runs,
    classify_execution,
    classify_memory_task,
    classify_plan,
    guard_cross_surface,
    parse_plan_settlement_key,
    plan_statement,
    usage_statement,
)

ROOT = Path(__file__).resolve().parents[1]


def _ask(owner, conversation, event_type, run_id, sequence):
    return AskEventFact(owner, conversation, event_type, run_id, sequence)


def _usage(owner, source, key):
    return UsageFact(owner, source, key)


def test_two_ask_rounds_in_one_conversation_keep_distinct_run_ids() -> None:
    owner = uuid.uuid4()
    conversation = uuid.uuid4()
    events = [
        _ask(owner, conversation, "chat.run.started", "run-round-1", 1),
        _ask(owner, conversation, "chat.done", "run-round-1", 2),
        _ask(owner, conversation, "chat.run.started", "run-round-2", 3),
        _ask(owner, conversation, "chat.done", "run-round-2", 4),
    ]
    usage = [
        _usage(owner, "chat", "run-round-1:1"),
        _usage(owner, "chat", "run-round-2:1"),
        _usage(owner, "chat", "run-round-2:rest"),
    ]
    found = classify_ask_runs(owner, events, usage)
    assert [item.external_id for item in found] == ["run-round-1", "run-round-2"]
    assert {item.identity_status for item in found} == {"resolved"}
    assert {item.conversation_id for item in found} == {str(conversation)}
    assert found[0].settlement_keys == ("run-round-1:1",)
    assert found[1].settlement_keys == ("run-round-2:1", "run-round-2:rest")
    assert found[0].external_id_type == "chat_run_id"


def test_repeated_ask_run_id_is_ambiguous_and_not_picked() -> None:
    owner = uuid.uuid4()
    first = uuid.uuid4()
    second = uuid.uuid4()
    events = [
        _ask(owner, first, "chat.run.started", "run-same01", 1),
        _ask(owner, first, "chat.done", "run-same01", 2),
        _ask(owner, second, "chat.run.started", "run-same01", 1),
        _ask(owner, second, "chat.done", "run-same01", 2),
    ]
    found = classify_ask_runs(owner, events, [])
    assert len(found) == 1
    assert found[0].identity_status == "ambiguous"
    assert found[0].reason == "duplicate_external_id"
    assert found[0].conversation_id is None
    assert found[0].external_id == "run-same01"
    assert len(found[0].candidates) == 2
    assert str(first) in " ".join(found[0].candidates)
    assert str(second) in " ".join(found[0].candidates)


def test_same_conversation_duplicate_start_is_also_ambiguous() -> None:
    owner = uuid.uuid4()
    conversation = uuid.uuid4()
    events = [
        _ask(owner, conversation, "chat.run.started", "run-dupestart", 1),
        _ask(owner, conversation, "chat.run.started", "run-dupestart", 4),
    ]
    found = classify_ask_runs(owner, events, [])
    assert found[0].identity_status == "ambiguous"
    assert found[0].conversation_id is None
    assert len(found[0].candidates) == 2


def test_other_users_ask_events_and_usage_are_not_visible() -> None:
    owner = uuid.uuid4()
    other = uuid.uuid4()
    conversation = uuid.uuid4()
    foreign = uuid.uuid4()
    events = [
        _ask(other, foreign, "chat.run.started", "run-secret", 1),
        _ask(owner, conversation, "chat.run.started", "run-secret", 1),
    ]
    usage = [_usage(other, "chat", "run-secret:1"), _usage(owner, "chat", "run-secret:1")]
    found = classify_ask_runs(owner, events, usage)
    assert len(found) == 1
    assert found[0].identity_status == "resolved"
    assert found[0].conversation_id == str(conversation)
    assert found[0].settlement_keys == ("run-secret:1",)


def test_ask_events_without_run_id_stay_unresolved() -> None:
    owner = uuid.uuid4()
    conversation = uuid.uuid4()
    found = classify_ask_runs(owner, [_ask(owner, conversation, "chat.done", None, 2)], [])
    assert found[0].identity_status == "unresolved"
    assert found[0].reason == "missing_run_id"
    assert found[0].external_id is None
    assert found[0].conversation_id == str(conversation)


def test_ask_settlement_suffix_does_not_attach_another_run() -> None:
    assert ask_settlement_belongs("run-a", "run-a:1")
    assert ask_settlement_belongs("run-a", "run-a:rest")
    assert not ask_settlement_belongs("run-a", "run-ab:1")
    assert not ask_settlement_belongs("run-a", "run-a:1:extra")


def test_plan_revisions_and_model_calls_are_different_layers() -> None:
    owner = uuid.uuid4()
    plan_id = uuid.uuid4()
    conversation = uuid.uuid4()
    plan = PlanFact(plan_id, "plan_abc", owner, conversation, 2)
    usage = [
        _usage(owner, "planning", "req-one:plan_abc:1"),
        _usage(owner, "planning", "req-two:plan_abc:2"),
        _usage(owner, "planning", "req-three:plan_other:2"),
        _usage(uuid.uuid4(), "planning", "req-foreign:plan_abc:2"),
    ]
    mapped = classify_plan(
        owner,
        plan,
        [RevisionFact(0), RevisionFact(1), RevisionFact(2)],
        [InteractionFact("card-1", 1)],
        usage,
    )
    assert mapped.plan.identity_status == "resolved"
    assert mapped.plan.external_id == "plan_abc"
    assert mapped.plan.external_id_type == "plan_public_id"
    assert mapped.plan.internal_id == str(plan_id)
    assert mapped.plan.external_id != mapped.plan.internal_id
    assert [item.revision for item in mapped.revisions] == [0, 1, 2]
    assert {item.external_id_type for item in mapped.revisions} == {"plan_revision"}
    assert mapped.plan.external_id_type != "plan_revision"
    assert [item.request_id for item in mapped.model_calls] == ["req-one", "req-two"]
    assert [item.external_id for item in mapped.model_calls] == ["req-one:plan_abc:1", "req-two:plan_abc:2"]
    assert mapped.model_calls[0].parent_external_id == "plan_abc"
    assert mapped.interactions[0].external_id == "card-1"
    assert mapped.interactions[0].parent_external_id == "plan_abc"
    assert mapped.interactions[0].external_id != mapped.plan.external_id


def test_plan_pointer_without_snapshot_is_not_rewritten() -> None:
    owner = uuid.uuid4()
    plan = PlanFact(uuid.uuid4(), "plan_missingrev", owner, uuid.uuid4(), 3)
    mapped = classify_plan(owner, plan, [RevisionFact(0), RevisionFact(1)], [], [])
    assert mapped.plan.identity_status == "ambiguous"
    assert mapped.plan.reason == "current_revision_not_in_history"
    assert mapped.plan.revision == 3
    assert [item.revision for item in mapped.revisions] == [0, 1]


def test_foreign_plan_is_not_found() -> None:
    owner = uuid.uuid4()
    plan = PlanFact(uuid.uuid4(), "plan_foreign", uuid.uuid4(), uuid.uuid4(), 0)
    mapped = classify_plan(owner, plan, [RevisionFact(0)], [], [])
    assert mapped.plan.identity_status == "unresolved"
    assert mapped.plan.reason == "not_found"
    assert mapped.plan.external_id is None


def test_agent_task_execution_and_request_stay_distinct() -> None:
    owner = uuid.uuid4()
    task_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    conversation = uuid.uuid4()
    task = AgentTaskFact(task_id, owner, conversation, "request-77")
    found = classify_agent_task(
        owner,
        task_id.hex,
        task,
        [ExecutionFact(execution_id, task_id)],
        [AgentEventFact(task_id, execution_id)],
        [_usage(owner, "agent", f"{task_id.hex}:1"), _usage(owner, "agent", f"{task_id.hex}:2"), _usage(owner, "chat", f"{task_id.hex}:9")],
    )
    assert found.identity_status == "resolved"
    assert found.external_id == task_id.hex
    assert found.external_id_type == "agent_task_id"
    assert found.execution_id == str(execution_id)
    assert found.external_id != found.execution_id
    assert found.request_id == "request-77"
    assert found.request_id != found.external_id
    assert found.conversation_id == str(conversation)
    assert found.settlement_keys == (f"{task_id.hex}:1", f"{task_id.hex}:2")
    execution = classify_execution(owner, task, ExecutionFact(execution_id, task_id))
    assert execution.external_id_type == "execution_id"
    assert execution.parent_external_id == task_id.hex
    looked_up_as_task = classify_agent_task(
        owner,
        str(execution_id),
        task,
        [ExecutionFact(execution_id, task_id)],
        [],
        [],
    )
    assert looked_up_as_task.identity_status == "unresolved"
    assert looked_up_as_task.reason == "execution_id_is_not_task_id"
    assert looked_up_as_task.external_id is None


def test_multiple_executions_are_not_reduced_to_one() -> None:
    owner = uuid.uuid4()
    task_id = uuid.uuid4()
    task = AgentTaskFact(task_id, owner, uuid.uuid4(), "request-1")
    first = uuid.uuid4()
    second = uuid.uuid4()
    found = classify_agent_task(
        owner,
        task_id.hex,
        task,
        [ExecutionFact(first, task_id), ExecutionFact(second, task_id)],
        [],
        [],
    )
    assert found.identity_status == "ambiguous"
    assert found.reason == "multiple_executions"
    assert found.execution_id is None
    assert str(first) in found.candidates
    assert str(second) in found.candidates


def test_other_users_agent_task_is_not_found() -> None:
    owner = uuid.uuid4()
    task = AgentTaskFact(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "request-x")
    found = classify_agent_task(owner, task.task_id.hex, task, [], [], [])
    assert found.identity_status == "unresolved"
    assert found.reason == "not_found"
    assert found.external_id is None


def test_memory_agent_has_no_conversation_or_execution() -> None:
    actor = str(uuid.uuid4())
    found = classify_memory_task(actor, MemoryTaskFact("mem-task-1", "request-m", actor))
    assert found.identity_status == "resolved"
    assert found.reason == "memory_only"
    assert found.durable is False
    assert found.conversation_id is None
    assert found.execution_id is None
    assert found.external_id_type == "memory_task_id"
    missing_owner = classify_memory_task(actor, MemoryTaskFact("mem-task-1", "request-m", None))
    assert missing_owner.identity_status == "unresolved"
    assert missing_owner.reason == "memory_owner_missing"
    other = classify_memory_task(actor, MemoryTaskFact("mem-task-1", "request-m", str(uuid.uuid4())))
    assert other.identity_status == "unresolved"
    assert other.external_id is None


def test_same_external_id_is_not_merged_across_surfaces() -> None:
    owner = uuid.uuid4()
    conversation = uuid.uuid4()
    shared = "shared-id-01"
    ask = classify_ask_runs(owner, [_ask(owner, conversation, "chat.run.started", shared, 1)], [])[0]
    plan = classify_plan(
        owner,
        PlanFact(uuid.uuid4(), shared, owner, conversation, 0),
        [RevisionFact(0)],
        [],
        [],
    ).plan
    blocked = guard_cross_surface([ask, plan])
    assert ask.identity_status == "resolved"
    assert plan.identity_status == "resolved"
    assert blocked is not None
    assert blocked.identity_status == "ambiguous"
    assert blocked.reason == "cross_surface"
    assert blocked.surface == "unscoped"
    assert blocked.external_id is None
    assert shared in blocked.candidates


def test_queries_are_scoped_to_the_authenticated_owner() -> None:
    owner = uuid.uuid4()
    conversation = uuid.uuid4()
    task_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    compiled = {
        "ask": ask_event_statement(owner, "run-aaaa"),
        "ask_conversation": ask_conversation_statement(owner, conversation),
        "plan": plan_statement(owner, "plan_abc"),
        "usage": usage_statement(owner, "chat"),
        "agent": agent_task_statement(owner, task_id),
        "execution": agent_execution_owner_statement(owner, execution_id),
    }
    for name, statement in compiled.items():
        sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert str(owner) in sql, name
        assert "user_id" in sql or "owner_id" in sql, name
    plan_sql = str(plan_statement(owner, "plan_abc").compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "plans.owner_id" in plan_sql
    assert "sessions.user_id" in plan_sql


def test_existing_settlement_key_literals_are_unchanged() -> None:
    chat = (ROOT / "app" / "api" / "v1" / "endpoints" / "chat.py").read_text(encoding="utf-8")
    planning = (ROOT / "app" / "planning" / "sql_store.py").read_text(encoding="utf-8")
    bridge = (ROOT / "app" / "authorization" / "application" / "quota_bridge.py").read_text(encoding="utf-8")
    assert 'settlement_key=f"{run_id}:{usage_round}"' in chat
    assert 'settlement_key=f"{run_id}:rest"' in chat
    assert 'settlement_key = f"{request_id}:{plan_id}:{revision}"' in planning
    assert 'settlement_key=f"{task_key}:{step}"' in bridge
    assert parse_plan_settlement_key("req:plan_abc:2") == ("req", "plan_abc", 2)
    identity = (ROOT / "app" / "runtime" / "run_identity.py").read_text(encoding="utf-8")
    assert "QuotaService" not in identity


def test_no_public_identity_route(client) -> None:
    response = client.get("/api/v1/run-identity")
    assert response.status_code == 404
