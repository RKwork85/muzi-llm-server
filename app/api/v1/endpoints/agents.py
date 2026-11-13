"""Agent 任务接口。客户端断开 SSE 只结束订阅，不取消运行。"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.models import AgentEventType, AgentRequest, EventEnvelope
from app.agents.task_manager import TaskRecord
from app.api.agent_access import authenticate_request_user, require_agent_operator
from app.authorization.application.execution_guard import CURRENT_EXECUTION, load_execution_context
from app.authorization.application.audit_service import QuotaService
from app.authorization.application.quota_bridge import quota_event_observer
from app.agents.runtime import AgentRuntime
from app.api.deps import get_agent_runtime
from app.api.v1.endpoints.mcp import optional_mcp
from app.mcp.service import McpService
from app.core.exceptions import AgentTaskNotFoundError, DatabaseUnavailableError
from app.core.context import get_request_id
from app.core.sse import format_sse
from app.schemas.agents import AgentTaskResponse
from app.tools.workspace_scope import scope_dirname
from app.core.database.session import get_session_factory
from app.models.tables import AgentTask, ExecutionEvent, TaskExecution, User
from app.services.auth_service import AuthService
from app.core.exceptions import LoginRequiredError, PlanningResourceNotFoundError
from app.planning.sql_store import SqlPlanningStore
from app.planning.runtime import PlanningNotFound
from app.agents.persistence import AgentRunPersistence
from app.core.context import get_request_id

router = APIRouter(prefix="/agents", tags=["agents"])
_optional_bearer = HTTPBearer(auto_error=False)

_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_TERMINAL_EVENTS = frozenset(
    {
        AgentEventType.COMPLETED,
        AgentEventType.FAILED,
        AgentEventType.CANCELLED,
        AgentEventType.LIMIT_REACHED,
    }
)


@router.post("/runs", response_model=AgentTaskResponse, summary="创建 Agent 运行")
async def create_run(
    body: AgentRequest,
    request: Request,
    runtime: AgentRuntime = Depends(get_agent_runtime),
    mcp: McpService | None = Depends(optional_mcp),
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_optional_bearer)] = None,
) -> AgentTaskResponse:
    owner = await require_agent_operator(credentials)
    user_id = await mcp.user_id_from_request(request) if mcp is not None else None
    if mcp is not None and user_id is not None:
        from app.mcp.grants import sync_stdio

        await sync_stdio(user_id, request.app.state.settings.mcp_server_name)
    request_id = get_request_id()
    task_id = None
    event_observer = None
    factory = None
    if body.conversation_id:
        try:
            factory = get_session_factory()
        except RuntimeError:
            raise DatabaseUnavailableError() from None
        async with factory() as session:
            try:
                conversation = await SqlPlanningStore(session)._conversation(body.conversation_id, owner.id)
            except PlanningNotFound:
                raise PlanningResourceNotFoundError() from None
            db_task_id = uuid.uuid4()
            task_id = db_task_id.hex
            execution_id = uuid.uuid4()
            session.add(
                AgentTask(
                    id=db_task_id,
                    user_id=owner.id,
                    session_id=conversation.id,
                    request_id=request_id,
                    task_type="agent",
                    status="created",
                    input_payload={"surface": "agent_runtime_api", "request": body.model_dump(mode="json")},
                )
            )
            session.add(
                TaskExecution(
                    id=execution_id,
                    task_id=db_task_id,
                    status="pending",
                    execution_input=body.model_dump(mode="json"),
                )
            )
            await session.commit()
        event_observer = AgentRunPersistence(
            factory,
            db_task_id,
            execution_id,
            lambda: runtime.get(task_id),
        )
    quota_user_id = None
    if getattr(request.app.state, "database_ready", False):
        try:
            quota_factory = factory or get_session_factory()
        except RuntimeError:
            raise DatabaseUnavailableError() from None
        async with quota_factory() as session:
            await QuotaService(session).authorize(owner.id)
            await session.commit()
        quota_user_id = str(owner.id)
    observer = quota_event_observer(event_observer, quota_user_id, task_id or request_id)
    workspace_scope = scope_dirname(request.headers.get("x-workspace-key"))
    execution = await load_execution_context(
        str(owner.id),
        runtime_mode="agent",
        run_id=task_id,
        conversation_id=body.conversation_id,
        workspace_scope=workspace_scope,
    )
    token = CURRENT_EXECUTION.set(execution)
    try:
        view = await runtime.start(
            body,
            request_id,
            workspace_scope=workspace_scope,
            mcp_user_id=user_id,
            mcp_service=mcp,
            task_id=task_id,
            event_observer=observer,
            owner_id=str(owner.id),
        )
    except Exception:
        if task_id:
            async with factory() as session:
                task = await session.get(AgentTask, uuid.UUID(hex=task_id))
                execution_row = await session.scalar(
                    select(TaskExecution).where(TaskExecution.task_id == uuid.UUID(hex=task_id))
                )
                if task is not None:
                    task.status = "failed"
                    task.error_code = "AGENT_START_FAILED"
                if execution_row is not None:
                    execution_row.status = "failed"
                    execution_row.error_code = "AGENT_START_FAILED"
                    execution_row.finished_at = datetime.now(UTC)
                await session.commit()
        raise
    finally:
        CURRENT_EXECUTION.reset(token)
    return AgentTaskResponse.model_validate(view)


@router.get("/runs/{task_id}", response_model=AgentTaskResponse, summary="查询 Agent 任务")
async def read_run(
    task_id: str,
    runtime: AgentRuntime = Depends(get_agent_runtime),
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_optional_bearer)] = None,
) -> AgentTaskResponse:
    _known_task(task_id)
    actor_id = await authenticate_request_user(credentials)
    record = _owned_memory_task(runtime, task_id, actor_id)
    if record is not None:
        return AgentTaskResponse.model_validate(record.view())
    await _verify_persisted_owner(task_id, actor_id)
    async with await _owned_persisted_task(task_id, credentials) as persisted:
        return AgentTaskResponse.model_validate(persisted.payload)


@router.post("/runs/{task_id}/cancel", response_model=AgentTaskResponse, summary="取消 Agent 运行")
async def cancel_run(
    task_id: str,
    runtime: AgentRuntime = Depends(get_agent_runtime),
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_optional_bearer)] = None,
) -> AgentTaskResponse:
    _known_task(task_id)
    actor_id = await authenticate_request_user(credentials)
    if _owned_memory_task(runtime, task_id, actor_id) is not None:
        view = await runtime.cancel(task_id)
        return AgentTaskResponse.model_validate(view)
    await _verify_persisted_owner(task_id, actor_id)
    try:
        view = await runtime.cancel(task_id)
    except AgentTaskNotFoundError:
        async with await _owned_persisted_task(task_id, credentials) as persisted:
            if persisted.payload.get("status") in {"created", "running"}:
                task_uuid = uuid.UUID(task_id)
                task = await persisted.session.get(AgentTask, task_uuid)
                execution = await persisted.session.scalar(
                    select(TaskExecution).where(TaskExecution.task_id == task_uuid).order_by(TaskExecution.created_at.desc())
                )
                if task is not None:
                    task.status = "cancelled"
                    task.error_code = "AGENT_CANCELLED"
                if execution is not None:
                    execution.status = "cancelled"
                    execution.error_code = "AGENT_CANCELLED"
                    execution.finished_at = datetime.now(UTC)
                sequence = await persisted.session.scalar(
                    select(func.coalesce(func.max(ExecutionEvent.sequence), 0)).where(ExecutionEvent.task_id == task_uuid)
                )
                envelope = EventEnvelope(
                    request_id=str(persisted.payload.get("request_id", task_id)),
                    task_id=task_id,
                    sequence=int(sequence or 0) + 1,
                    timestamp=datetime.now(UTC).isoformat(),
                    type=AgentEventType.CANCELLED,
                    data={"error_code": "AGENT_CANCELLED"},
                )
                if execution is not None:
                    persisted.session.add(
                        ExecutionEvent(
                            task_id=task_uuid,
                            execution_id=execution.id,
                            event_type=AgentEventType.CANCELLED.value,
                            payload=envelope.model_dump(mode="json"),
                            sequence=envelope.sequence,
                        )
                    )
                await persisted.session.commit()
                persisted.payload.update(status="cancelled", error_code="AGENT_CANCELLED")
            view = persisted.payload
    return AgentTaskResponse.model_validate(view)


@router.get("/runs/{task_id}/events", summary="订阅 Agent 事件")
async def stream_run(
    task_id: str,
    request: Request,
    runtime: AgentRuntime = Depends(get_agent_runtime),
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_optional_bearer)] = None,
) -> StreamingResponse:
    _known_task(task_id)
    actor_id = await authenticate_request_user(credentials)
    if _owned_memory_task(runtime, task_id, actor_id) is None:
        await _verify_persisted_owner(task_id, actor_id)
    try:
        publisher = runtime.publisher_for(task_id)
    except AgentTaskNotFoundError:
        return StreamingResponse(
            _persisted_frames(task_id, request, credentials),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return StreamingResponse(
        _frames(request, publisher),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _known_task(task_id: str) -> None:
    if not _TASK_ID.fullmatch(task_id):
        raise AgentTaskNotFoundError()


def _owned_memory_task(runtime: AgentRuntime, task_id: str, actor_id: str) -> TaskRecord | None:
    """内存任务按服务端记录的所有者比对，不看 task_id 是否为 UUID。"""
    try:
        record = runtime.tasks.get(task_id)
    except AgentTaskNotFoundError:
        return None
    if record.owner_id != actor_id:
        raise AgentTaskNotFoundError()
    return record


class _OwnedPersistedTask:
    def __init__(self, session: AsyncSession, payload: dict[str, object]) -> None:
        self.session = session
        self.payload = payload

    async def __aenter__(self) -> _OwnedPersistedTask:
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.session.close()


async def _owned_persisted_task(
    task_id: str,
    credentials: HTTPAuthorizationCredentials | None,
) -> _OwnedPersistedTask:
    if credentials is None:
        raise AgentTaskNotFoundError()
    try:
        task_uuid = uuid.UUID(task_id)
        factory = get_session_factory()
    except (ValueError, RuntimeError):
        raise AgentTaskNotFoundError() from None
    session = factory()
    owner = await AuthService(session).user_for_token(credentials.credentials)
    task = await session.get(AgentTask, task_uuid)
    if owner is None or task is None or task.user_id != owner.id:
        await session.close()
        raise AgentTaskNotFoundError()
    payload = dict(task.output_payload or {})
    payload.setdefault("task_id", task_id)
    payload.setdefault("request_id", task.request_id or task_id)
    payload.setdefault("status", task.status)
    payload.setdefault("answer", None)
    payload.setdefault("steps", 0)
    payload.setdefault("tool_calls", 0)
    payload.setdefault("error_code", task.error_code)
    return _OwnedPersistedTask(session, payload)


async def _verify_persisted_owner(task_id: str, actor_id: str) -> None:
    try:
        task_uuid = uuid.UUID(task_id)
    except ValueError:
        raise AgentTaskNotFoundError() from None
    try:
        factory = get_session_factory()
    except RuntimeError:
        raise DatabaseUnavailableError() from None
    async with factory() as session:
        task = await session.get(AgentTask, task_uuid)
        if task is None or str(task.user_id) != actor_id:
            raise AgentTaskNotFoundError()


async def _persisted_frames(
    task_id: str,
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
):
    owned = await _owned_persisted_task(task_id, credentials)
    session = owned.session
    task_uuid = uuid.UUID(task_id)
    try:
        sequence = 0
        while True:
            if await request.is_disconnected():
                break
            events = list(
                await session.scalars(
                    select(ExecutionEvent)
                    .where(ExecutionEvent.task_id == task_uuid, ExecutionEvent.sequence > sequence)
                    .order_by(ExecutionEvent.sequence)
                )
            )
            for event in events:
                sequence = event.sequence
                envelope = event.payload
                event_type = str(envelope.get("type", event.event_type))
                yield format_sse(event_type, envelope)
                if event_type in {item.value for item in _TERMINAL_EVENTS}:
                    return
            yield ": heartbeat\n\n"
            await asyncio.sleep(1)
    finally:
        await session.close()


async def _frames(request: Request, publisher) -> object:
    queue = publisher.subscribe()
    try:
        yield ": heartbeat\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
            except TimeoutError:
                yield ": heartbeat\n\n"
                continue
            yield format_sse(event.type.value, event.model_dump(mode="json"))
            if event.type in _TERMINAL_EVENTS:
                break
    finally:
        publisher.unsubscribe(queue)
