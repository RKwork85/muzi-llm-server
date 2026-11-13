"""Ask、Plan、Agent 的只读身份对照。

结果不是数据库里的 Run 表，也不会被 SSE 或结算使用。
无法唯一确定时返回 ambiguous 或 unresolved，不挑选其中一条。
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.planning import PersistentInteraction, PersistentPlan, PersistentPlanRevision, PersistentPlanningEvent
from app.models.rbac import UsageRecord
from app.models.tables import AgentTask, ConversationSession, ExecutionEvent, TaskExecution

IdentityStatus = Literal["resolved", "unresolved", "ambiguous"]

_CHAT_EVENTS = (
    "chat.run.started",
    "chat.done",
    "chat.error",
    "chat.cancelled",
)
_ASK_SOURCES = ("chat",)
_PLAN_SOURCE = "planning"
_AGENT_SOURCE = "agent"


@dataclass(frozen=True)
class RunIdentity:
    """候选对照结构。字段多出来的部分表示现有数据里确实需要区分的身份。"""

    surface: Literal["ask", "plan", "agent", "unscoped"]
    owner_id: str
    conversation_id: str | None
    external_id: str | None
    external_id_type: str | None
    parent_external_id: str | None
    revision: int | None
    execution_id: str | None
    request_id: str | None
    settlement_keys: tuple[str, ...]
    identity_status: IdentityStatus
    reason: str | None
    internal_id: str | None = None
    durable: bool = True
    candidates: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        body = asdict(self)
        body["settlement_keys"] = list(self.settlement_keys)
        body["candidates"] = list(self.candidates)
        return body


@dataclass(frozen=True)
class AskEventFact:
    owner_id: uuid.UUID
    conversation_id: uuid.UUID
    event_type: str
    run_id: str | None
    sequence: int


@dataclass(frozen=True)
class UsageFact:
    user_id: uuid.UUID
    source: str
    settlement_key: str | None


@dataclass(frozen=True)
class PlanFact:
    plan_id: uuid.UUID
    public_id: str
    owner_id: uuid.UUID
    conversation_id: uuid.UUID
    current_revision: int


@dataclass(frozen=True)
class RevisionFact:
    revision: int


@dataclass(frozen=True)
class InteractionFact:
    public_id: str
    plan_revision: int


@dataclass(frozen=True)
class AgentTaskFact:
    task_id: uuid.UUID
    user_id: uuid.UUID
    session_id: uuid.UUID | None
    request_id: str | None


@dataclass(frozen=True)
class ExecutionFact:
    execution_id: uuid.UUID
    task_id: uuid.UUID


@dataclass(frozen=True)
class AgentEventFact:
    task_id: uuid.UUID
    execution_id: uuid.UUID | None


@dataclass(frozen=True)
class MemoryTaskFact:
    task_id: str
    request_id: str
    owner_id: str | None


@dataclass(frozen=True)
class PlanIdentityMap:
    plan: RunIdentity
    revisions: tuple[RunIdentity, ...]
    model_calls: tuple[RunIdentity, ...]
    interactions: tuple[RunIdentity, ...]


def ask_event_statement(owner_id: uuid.UUID, run_id: str):
    return (
        select(PersistentPlanningEvent, ConversationSession.user_id)
        .join(ConversationSession, ConversationSession.id == PersistentPlanningEvent.conversation_id)
        .where(
            ConversationSession.user_id == owner_id,
            PersistentPlanningEvent.event_type.in_(_CHAT_EVENTS),
            PersistentPlanningEvent.payload["run_id"].as_string() == run_id,
        )
        .order_by(PersistentPlanningEvent.conversation_id, PersistentPlanningEvent.sequence)
    )


def ask_conversation_statement(owner_id: uuid.UUID, conversation_id: uuid.UUID):
    return (
        select(PersistentPlanningEvent, ConversationSession.user_id)
        .join(ConversationSession, ConversationSession.id == PersistentPlanningEvent.conversation_id)
        .where(
            ConversationSession.user_id == owner_id,
            ConversationSession.id == conversation_id,
            PersistentPlanningEvent.event_type.in_(_CHAT_EVENTS),
        )
        .order_by(PersistentPlanningEvent.sequence)
    )


def plan_statement(owner_id: uuid.UUID, public_id: str):
    return (
        select(PersistentPlan)
        .join(ConversationSession, PersistentPlan.conversation_id == ConversationSession.id)
        .where(
            PersistentPlan.public_id == public_id,
            PersistentPlan.owner_id == owner_id,
            ConversationSession.user_id == owner_id,
        )
    )


def plan_revision_statement(plan_id: uuid.UUID):
    return select(PersistentPlanRevision).where(PersistentPlanRevision.plan_id == plan_id)


def plan_interaction_statement(plan_id: uuid.UUID):
    return select(PersistentInteraction).where(PersistentInteraction.plan_id == plan_id)


def usage_statement(owner_id: uuid.UUID, source: str):
    return select(UsageRecord).where(UsageRecord.user_id == owner_id, UsageRecord.source == source)


def agent_task_statement(owner_id: uuid.UUID, task_id: uuid.UUID):
    return select(AgentTask).where(AgentTask.id == task_id, AgentTask.user_id == owner_id)


def agent_execution_statement(task_id: uuid.UUID):
    return select(TaskExecution).where(TaskExecution.task_id == task_id)


def agent_execution_owner_statement(owner_id: uuid.UUID, execution_id: uuid.UUID):
    return (
        select(TaskExecution)
        .join(AgentTask, AgentTask.id == TaskExecution.task_id)
        .where(TaskExecution.id == execution_id, AgentTask.user_id == owner_id)
    )


def agent_event_statement(task_id: uuid.UUID):
    return select(ExecutionEvent).where(ExecutionEvent.task_id == task_id)


def classify_ask_runs(
    owner_id: uuid.UUID,
    events: list[AskEventFact],
    usage: list[UsageFact] | None = None,
) -> list[RunIdentity]:
    """按 run_id 分组。同一用户的同一个 run_id 出现多次开始事件时不挑选会话。"""

    owned = [item for item in events if item.owner_id == owner_id]
    usage_rows = [item for item in usage or [] if item.user_id == owner_id and item.source in _ASK_SOURCES]
    grouped: dict[str, list[AskEventFact]] = {}
    missing: list[AskEventFact] = []
    for item in owned:
        if item.event_type not in _CHAT_EVENTS:
            continue
        if not item.run_id:
            missing.append(item)
            continue
        grouped.setdefault(item.run_id, []).append(item)

    found = [_classify_one_ask(owner_id, run_id, rows, usage_rows) for run_id, rows in sorted(grouped.items())]
    if missing:
        found.append(_missing_ask(owner_id, missing))
    return found


def classify_plan(
    owner_id: uuid.UUID,
    plan: PlanFact | None,
    revisions: list[RevisionFact],
    interactions: list[InteractionFact],
    usage: list[UsageFact],
) -> PlanIdentityMap:
    if plan is None or plan.owner_id != owner_id or not plan.public_id:
        empty = _status(
            "plan",
            owner_id,
            status="unresolved",
            reason="not_found" if plan is None or plan.owner_id != owner_id else "public_id_missing",
        )
        return PlanIdentityMap(empty, (), (), ())

    numbers = [item.revision for item in revisions]
    pointer_missing = plan.current_revision not in numbers
    plan_view = _status(
        "plan",
        owner_id,
        status="ambiguous" if pointer_missing else "resolved",
        reason="current_revision_not_in_history" if pointer_missing else None,
        conversation_id=str(plan.conversation_id),
        external_id=plan.public_id,
        external_id_type="plan_public_id",
        revision=plan.current_revision,
        internal_id=str(plan.plan_id),
    )
    revision_views = tuple(
        _status(
            "plan",
            owner_id,
            status="resolved",
            conversation_id=str(plan.conversation_id),
            external_id=plan.public_id,
            external_id_type="plan_revision",
            parent_external_id=plan.public_id,
            revision=number,
            internal_id=str(plan.plan_id),
        )
        for number in sorted(set(numbers))
    )
    calls = []
    for item in usage:
        if item.user_id != owner_id or item.source != _PLAN_SOURCE or not item.settlement_key:
            continue
        parsed = parse_plan_settlement_key(item.settlement_key)
        if parsed is None or parsed[1] != plan.public_id:
            continue
        request_id, _public_id, revision = parsed
        known = revision in numbers
        calls.append(
            _status(
                "plan",
                owner_id,
                status="resolved" if known else "ambiguous",
                reason=None if known else "revision_not_in_history",
                conversation_id=str(plan.conversation_id),
                external_id=item.settlement_key,
                external_id_type="planning_settlement_key",
                parent_external_id=plan.public_id,
                revision=revision,
                request_id=request_id,
                settlement_keys=(item.settlement_key,),
                internal_id=str(plan.plan_id),
            )
        )
    interaction_views = _classify_interactions(owner_id, plan, interactions)
    return PlanIdentityMap(plan_view, revision_views, tuple(calls), interaction_views)


def classify_agent_task(
    owner_id: uuid.UUID,
    queried_id: str,
    task: AgentTaskFact | None,
    executions: list[ExecutionFact],
    events: list[AgentEventFact],
    usage: list[UsageFact] | None = None,
) -> RunIdentity:
    """用任务号查询。传入 execution_id 时不把它升级成任务身份。"""

    if task is None or task.user_id != owner_id:
        if _execution_matches_query(queried_id, executions, task):
            return _status("agent", owner_id, status="unresolved", reason="execution_id_is_not_task_id")
        return _status("agent", owner_id, status="unresolved", reason="not_found")
    public_task_id = task.task_id.hex
    if not _same_task_query(queried_id, task.task_id):
        if _execution_matches_query(queried_id, executions, task):
            return _status("agent", owner_id, status="unresolved", reason="execution_id_is_not_task_id")
        return _status("agent", owner_id, status="unresolved", reason="not_found")

    owned_executions = [item for item in executions if item.task_id == task.task_id]
    event_task_ids = {item.task_id for item in events}
    if event_task_ids and event_task_ids != {task.task_id}:
        return _status(
            "agent",
            owner_id,
            status="ambiguous",
            reason="event_task_mismatch",
            conversation_id=_optional_str(task.session_id),
            external_id=public_task_id,
            external_id_type="agent_task_id",
            request_id=task.request_id,
            internal_id=str(task.task_id),
            candidates=tuple(sorted(str(item) for item in event_task_ids)),
        )
    keys = _agent_keys(owner_id, public_task_id, usage or [])
    if len(owned_executions) > 1:
        return _status(
            "agent",
            owner_id,
            status="ambiguous",
            reason="multiple_executions",
            conversation_id=_optional_str(task.session_id),
            external_id=public_task_id,
            external_id_type="agent_task_id",
            request_id=task.request_id,
            settlement_keys=keys,
            internal_id=str(task.task_id),
            candidates=tuple(sorted(str(item.execution_id) for item in owned_executions)),
        )
    execution_id = str(owned_executions[0].execution_id) if len(owned_executions) == 1 else None
    return _status(
        "agent",
        owner_id,
        status="resolved",
        reason=None if execution_id else "execution_not_recorded",
        conversation_id=_optional_str(task.session_id),
        external_id=public_task_id,
        external_id_type="agent_task_id",
        execution_id=execution_id,
        request_id=task.request_id,
        settlement_keys=keys,
        internal_id=str(task.task_id),
    )


def classify_execution(
    owner_id: uuid.UUID,
    task: AgentTaskFact | None,
    execution: ExecutionFact | None,
) -> RunIdentity:
    if task is None or execution is None or task.user_id != owner_id or execution.task_id != task.task_id:
        return _status("agent", owner_id, status="unresolved", reason="not_found")
    return _status(
        "agent",
        owner_id,
        status="resolved",
        conversation_id=_optional_str(task.session_id),
        external_id=str(execution.execution_id),
        external_id_type="execution_id",
        parent_external_id=task.task_id.hex,
        request_id=task.request_id,
        internal_id=str(task.task_id),
    )


def classify_memory_task(actor_id: str, task: MemoryTaskFact | None) -> RunIdentity:
    if task is None or not task.owner_id or task.owner_id != actor_id:
        reason = "memory_owner_missing" if task is not None and not task.owner_id else "not_found"
        return _status("agent", actor_id, status="unresolved", reason=reason, durable=False)
    return _status(
        "agent",
        actor_id,
        status="resolved",
        reason="memory_only",
        external_id=task.task_id,
        external_id_type="memory_task_id",
        request_id=task.request_id,
        durable=False,
    )


def guard_cross_surface(views: list[RunIdentity]) -> RunIdentity | None:
    """同一字符串已经在两种模式里解析成功时，不合并成一条。"""

    grouped: dict[str, set[str]] = {}
    for item in views:
        if item.identity_status != "resolved" or not item.external_id:
            continue
        grouped.setdefault(item.external_id, set()).add(item.surface)
    collisions = tuple(sorted(key for key, surfaces in grouped.items() if len(surfaces) > 1))
    if not collisions:
        return None
    owner = views[0].owner_id if views else ""
    return _status(
        "unscoped",
        owner,
        status="ambiguous",
        reason="cross_surface",
        candidates=collisions,
    )


def parse_plan_settlement_key(key: str) -> tuple[str, str, int] | None:
    """现有键是 `{request_id}:{plan_public_id}:{revision}`。解析失败就不归属。"""

    head, separator, revision_text = key.rpartition(":")
    if not separator or not revision_text.isdigit():
        return None
    request_id, separator, public_id = head.rpartition(":")
    if not separator or not request_id or not public_id.startswith("plan_"):
        return None
    return request_id, public_id, int(revision_text)


def ask_settlement_belongs(run_id: str, key: str | None) -> bool:
    if not run_id or not key:
        return False
    prefix = f"{run_id}:"
    if not key.startswith(prefix):
        return False
    suffix = key[len(prefix) :]
    return suffix == "rest" or suffix.isdigit()


async def load_ask_identity(session: AsyncSession, owner_id: uuid.UUID, run_id: str) -> list[RunIdentity]:
    event_rows = (await session.execute(ask_event_statement(owner_id, run_id))).all()
    usage_rows = (await session.scalars(usage_statement(owner_id, "chat"))).all()
    found = classify_ask_runs(
        owner_id,
        [_ask_fact(row, user_id) for row, user_id in event_rows],
        [_usage_fact(row) for row in usage_rows],
    )
    if found:
        return found
    return [
        _status(
            "ask",
            owner_id,
            status="unresolved",
            reason="not_found",
            external_id=run_id,
            external_id_type="chat_run_id",
        )
    ]


async def load_plan_identity(session: AsyncSession, owner_id: uuid.UUID, public_id: str) -> PlanIdentityMap:
    plan = await session.scalar(plan_statement(owner_id, public_id))
    if plan is None:
        return classify_plan(owner_id, None, [], [], [])
    revisions = (await session.scalars(plan_revision_statement(plan.id))).all()
    interactions = (await session.scalars(plan_interaction_statement(plan.id))).all()
    usage_rows = (await session.scalars(usage_statement(owner_id, _PLAN_SOURCE))).all()
    return classify_plan(
        owner_id,
        PlanFact(plan.id, plan.public_id, plan.owner_id, plan.conversation_id, plan.current_revision),
        [RevisionFact(item.revision) for item in revisions],
        [InteractionFact(item.public_id, item.plan_revision) for item in interactions],
        [_usage_fact(item) for item in usage_rows],
    )


async def load_agent_identity(session: AsyncSession, owner_id: uuid.UUID, queried_id: str) -> RunIdentity:
    task_uuid = _parse_uuid(queried_id)
    task = await session.scalar(agent_task_statement(owner_id, task_uuid)) if task_uuid else None
    executions: list[ExecutionFact] = []
    events: list[AgentEventFact] = []
    if task is not None:
        execution_rows = (await session.scalars(agent_execution_statement(task.id))).all()
        event_rows = (await session.scalars(agent_event_statement(task.id))).all()
        executions = [ExecutionFact(item.id, item.task_id) for item in execution_rows]
        events = [AgentEventFact(item.task_id, item.execution_id) for item in event_rows]
    elif task_uuid is not None:
        matched = await session.scalar(agent_execution_owner_statement(owner_id, task_uuid))
        if matched is not None:
            executions = [ExecutionFact(matched.id, matched.task_id)]
    usage_rows = (await session.scalars(usage_statement(owner_id, _AGENT_SOURCE))).all()
    fact = None if task is None else AgentTaskFact(task.id, task.user_id, task.session_id, task.request_id)
    return classify_agent_task(owner_id, queried_id, fact, executions, events, [_usage_fact(item) for item in usage_rows])


def _classify_one_ask(
    owner_id: uuid.UUID,
    run_id: str,
    rows: list[AskEventFact],
    usage: list[UsageFact],
) -> RunIdentity:
    starts = [item for item in rows if item.event_type == "chat.run.started"]
    keys = tuple(sorted({item.settlement_key for item in usage if item.settlement_key and ask_settlement_belongs(run_id, item.settlement_key)}))
    start_keys = tuple(sorted({f"{item.conversation_id}:{item.sequence}" for item in starts}))
    if len(starts) != 1:
        return _status(
            "ask",
            owner_id,
            status="ambiguous" if starts else "unresolved",
            reason="duplicate_external_id" if starts else "start_event_missing",
            external_id=run_id,
            external_id_type="chat_run_id",
            settlement_keys=keys,
            candidates=start_keys,
        )
    start = starts[0]
    return _status(
        "ask",
        owner_id,
        status="resolved",
        conversation_id=str(start.conversation_id),
        external_id=run_id,
        external_id_type="chat_run_id",
        request_id=None,
        settlement_keys=keys,
    )


def _missing_ask(owner_id: uuid.UUID, rows: list[AskEventFact]) -> RunIdentity:
    conversations = tuple(sorted({str(item.conversation_id) for item in rows}))
    return _status(
        "ask",
        owner_id,
        status="unresolved",
        reason="missing_run_id",
        conversation_id=conversations[0] if len(conversations) == 1 else None,
        external_id_type="chat_run_id",
        candidates=conversations if len(conversations) > 1 else (),
    )


def _classify_interactions(
    owner_id: uuid.UUID,
    plan: PlanFact,
    interactions: list[InteractionFact],
) -> tuple[RunIdentity, ...]:
    counts: dict[str, int] = {}
    for item in interactions:
        counts[item.public_id] = counts.get(item.public_id, 0) + 1
    views = []
    for item in interactions:
        duplicated = counts[item.public_id] > 1
        views.append(
            _status(
                "plan",
                owner_id,
                status="ambiguous" if duplicated else "resolved",
                reason="duplicate_external_id" if duplicated else None,
                conversation_id=str(plan.conversation_id),
                external_id=None if duplicated else item.public_id,
                external_id_type="plan_interaction",
                parent_external_id=plan.public_id,
                revision=item.plan_revision,
                internal_id=str(plan.plan_id),
                candidates=(item.public_id,) if duplicated else (),
            )
        )
    return tuple(views)


def _execution_matches_query(queried_id: str, executions: list[ExecutionFact], task: AgentTaskFact | None) -> bool:
    parsed = _parse_uuid(queried_id)
    if parsed is None:
        return False
    for item in executions:
        if item.execution_id != parsed:
            continue
        if task is None or item.task_id == task.task_id:
            return item.execution_id != (task.task_id if task else None)
    return False


def _same_task_query(queried_id: str, task_id: uuid.UUID) -> bool:
    parsed = _parse_uuid(queried_id)
    return parsed == task_id or queried_id == task_id.hex


def _agent_keys(owner_id: uuid.UUID, task_key: str, usage: list[UsageFact]) -> tuple[str, ...]:
    prefix = f"{task_key}:"
    keys = []
    for item in usage:
        if item.user_id != owner_id or item.source != _AGENT_SOURCE or not item.settlement_key:
            continue
        if item.settlement_key.startswith(prefix) and item.settlement_key[len(prefix) :].isdigit():
            keys.append(item.settlement_key)
    return tuple(sorted(set(keys)))


def _ask_fact(row: PersistentPlanningEvent, user_id: uuid.UUID) -> AskEventFact:
    payload = row.payload if isinstance(row.payload, dict) else {}
    run_id = payload.get("run_id")
    return AskEventFact(user_id, row.conversation_id, row.event_type, str(run_id) if run_id else None, row.sequence)


def _usage_fact(row: UsageRecord) -> UsageFact:
    return UsageFact(row.user_id, row.source, row.settlement_key)


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


def _optional_str(value: uuid.UUID | None) -> str | None:
    return None if value is None else str(value)


def _status(
    surface: Literal["ask", "plan", "agent", "unscoped"],
    owner_id: uuid.UUID | str,
    *,
    status: IdentityStatus,
    reason: str | None = None,
    conversation_id: str | None = None,
    external_id: str | None = None,
    external_id_type: str | None = None,
    parent_external_id: str | None = None,
    revision: int | None = None,
    execution_id: str | None = None,
    request_id: str | None = None,
    settlement_keys: tuple[str, ...] = (),
    internal_id: str | None = None,
    durable: bool = True,
    candidates: tuple[str, ...] = (),
) -> RunIdentity:
    return RunIdentity(
        surface=surface,
        owner_id=str(owner_id),
        conversation_id=conversation_id,
        external_id=external_id,
        external_id_type=external_id_type,
        parent_external_id=parent_external_id,
        revision=revision,
        execution_id=execution_id,
        request_id=request_id,
        settlement_keys=settlement_keys,
        identity_status=status,
        reason=reason,
        internal_id=internal_id,
        durable=durable,
        candidates=candidates,
    )
