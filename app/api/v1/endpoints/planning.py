"""Authenticated conversation and Interactive Planning persistence APIs."""

from __future__ import annotations

import uuid
from typing import Annotated
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.core.sse import format_sse

from app.api.deps import get_authenticated_user
from app.api.deps import get_agent_runtime
from app.core.database.deps import DbSession
from app.core.database.session import get_session_factory
from app.core.exceptions import (
    PlanningConflictError,
    PlanningPolicyErrorResponse,
    PlanningResourceNotFoundError,
)
from app.models.tables import User
from app.models.tables import AgentTask, TaskExecution
from app.agents.models import AgentRequest
from app.agents.persistence import AgentRunPersistence
from app.agents.runtime import AgentRuntime
from app.agents.models import TaskStatus
from app.api.v1.endpoints.mcp import optional_mcp
from app.mcp.service import McpService
from app.core.context import get_request_id
from app.tools.workspace_scope import scope_dirname
from app.planning.api_schemas import (
    ConversationCreate,
    ConversationModeUpdate,
    PlanCreate,
    PlanEventList,
    PlanMessage,
    PlanRuntimeResponse,
)
from app.planning.contracts import (
    ExecutionAuthorizationRequest,
    InteractionAction,
    InteractionSubmission,
    PlanApprovalRequest,
    PlanStatus,
    PlanningEventType,
)
from app.planning import revisions
from app.planning.runtime import PlanningConflict, PlanningNotFound, PlanningPolicyError
from app.planning.sql_store import SqlPlanningStore

router = APIRouter(tags=["planning"])
AuthenticatedUser = Annotated[User, Depends(get_authenticated_user)]


def _translate(exc: Exception) -> None:
    if isinstance(exc, PlanningNotFound):
        raise PlanningResourceNotFoundError() from None
    if isinstance(exc, PlanningConflict):
        raise PlanningConflictError(str(exc)) from None
    if isinstance(exc, PlanningPolicyError):
        raise PlanningPolicyErrorResponse(str(exc)) from None
    raise exc


@router.post("/conversations", status_code=201, summary="创建持久化会话")
async def create_conversation(body: ConversationCreate, session: DbSession, user: AuthenticatedUser) -> dict[str, object]:
    return await SqlPlanningStore(session).create_conversation(user, body)


@router.get("/conversations/{conversation_id}", summary="读取会话快照")
async def read_conversation(conversation_id: str, session: DbSession, user: AuthenticatedUser) -> dict[str, object]:
    try:
        return await SqlPlanningStore(session).get_conversation(conversation_id, user)
    except (PlanningNotFound, PlanningConflict, PlanningPolicyError) as exc:
        _translate(exc)


@router.get("/conversations/{conversation_id}/events", response_model=PlanEventList, summary="按序号恢复会话事件")
async def conversation_events(
    conversation_id: str,
    session: DbSession,
    user: AuthenticatedUser,
    after: int = Query(default=0, ge=0),
) -> PlanEventList:
    try:
        events, next_sequence = await SqlPlanningStore(session).conversation_events_after(
            conversation_id, user, after
        )
        return PlanEventList(events=events, next_sequence=next_sequence)
    except (PlanningNotFound, PlanningConflict, PlanningPolicyError) as exc:
        _translate(exc)


@router.post("/conversations/{conversation_id}/mode", summary="独立切换会话模式")
async def set_conversation_mode(
    conversation_id: str,
    body: ConversationModeUpdate,
    session: DbSession,
    user: AuthenticatedUser,
) -> dict[str, object]:
    try:
        return await SqlPlanningStore(session).set_conversation_mode(conversation_id, user, body)
    except (PlanningNotFound, PlanningConflict, PlanningPolicyError) as exc:
        _translate(exc)


@router.post("/plans", response_model=PlanRuntimeResponse, status_code=201, summary="创建计划")
async def create_plan(body: PlanCreate, session: DbSession, user: AuthenticatedUser) -> PlanRuntimeResponse:
    try:
        plan = await SqlPlanningStore(session).create_plan(user, body.conversation_id, body.goal)
        return PlanRuntimeResponse(plan=plan)
    except (PlanningNotFound, PlanningConflict, PlanningPolicyError) as exc:
        _translate(exc)


@router.get("/plans/{plan_id}", response_model=PlanRuntimeResponse, summary="读取计划快照")
async def read_plan(plan_id: str, session: DbSession, user: AuthenticatedUser) -> PlanRuntimeResponse:
    try:
        store = SqlPlanningStore(session)
        plan = await store.get_plan(plan_id, user)
        interaction = await store.get_pending_interaction(plan_id, user)
        approved_revision = await store.get_approved_revision(plan_id, user)
        return PlanRuntimeResponse(plan=plan, interaction=interaction, approved_revision=approved_revision)
    except (PlanningNotFound, PlanningConflict, PlanningPolicyError) as exc:
        _translate(exc)


@router.get("/plans/{plan_id}/revisions", summary="列出计划历史版本")
async def list_plan_revisions(plan_id: str, session: DbSession, user: AuthenticatedUser) -> dict[str, object]:
    try:
        revisions_list = await SqlPlanningStore(session).list_revisions(plan_id, user)
        return {"revisions": revisions_list}
    except (PlanningNotFound, PlanningConflict, PlanningPolicyError) as exc:
        _translate(exc)


@router.get("/plans/{plan_id}/revisions/{revision}/diff", summary="对比两个版本的计划差异")
async def plan_revision_diff(
    plan_id: str,
    revision: int,
    session: DbSession,
    user: AuthenticatedUser,
) -> dict[str, object]:
    try:
        store = SqlPlanningStore(session)
        current = await store.get_plan(plan_id, user)
        target = await store.get_revision_snapshot(plan_id, user, revision)
        return {"diff": revisions.compute_plan_diff(target, current)}
    except (PlanningNotFound, PlanningConflict, PlanningPolicyError) as exc:
        _translate(exc)


@router.post("/plans/{plan_id}/messages", response_model=PlanRuntimeResponse, summary="继续规划对话")
async def plan_message(
    plan_id: str,
    body: PlanMessage,
    request: Request,
    session: DbSession,
    user: AuthenticatedUser,
) -> PlanRuntimeResponse:
    try:
        plan, interaction, response = await SqlPlanningStore(session).process_message(
            plan_id,
            user,
            body.expected_revision,
            body.message,
            request.app.state.provider,
            request.app.state.settings,
        )
        return PlanRuntimeResponse(plan=plan, interaction=interaction, response=response)
    except (PlanningNotFound, PlanningConflict, PlanningPolicyError) as exc:
        _translate(exc)


@router.post(
    "/plans/{plan_id}/messages/stream",
    response_class=StreamingResponse,
    summary="流式继续规划对话",
)
async def plan_message_stream(
    plan_id: str,
    body: PlanMessage,
    request: Request,
    session: DbSession,
    user: AuthenticatedUser,
) -> StreamingResponse:
    """与 `/messages` 同语义，但以 SSE 流式返回。

    事件顺序：说明文字逐段发 `event: delta`；随后 `event: plan`（计划快照更新）、
    若有交互卡片发 `event: interaction`；最后 `event: done`。流开始前的校验错误发 `event: error`。
    """
    store = SqlPlanningStore(session)

    async def stream_body():
        try:
            async for frame in store.process_message_stream(
                plan_id,
                user,
                body.expected_revision,
                body.message,
                request.app.state.provider,
                request.app.state.settings,
            ):
                yield frame
            await session.commit()
        except PlanningConflict as exc:
            yield format_sse("error", {"error": {"code": "PLANNING_CONFLICT", "message": str(exc)}})
            await session.rollback()
        except PlanningNotFound:
            yield format_sse("error", {"error": {"code": "PLANNING_NOT_FOUND", "message": "计划不存在"}})
            await session.rollback()
        except PlanningPolicyError as exc:
            yield format_sse("error", {"error": {"code": "PLANNING_POLICY_REJECTED", "message": str(exc)}})
            await session.rollback()

    return StreamingResponse(
        stream_body(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/plans/{plan_id}/interactions/{interaction_id}/submissions", response_model=PlanRuntimeResponse, summary="提交交互卡片")
async def submit_interaction(
    plan_id: str,
    interaction_id: str,
    body: InteractionSubmission,
    session: DbSession,
    user: AuthenticatedUser,
) -> PlanRuntimeResponse:
    if interaction_id != body.interaction_id:
        raise PlanningPolicyErrorResponse("interaction id does not match request path")
    try:
        plan = await SqlPlanningStore(session).submit_interaction(plan_id, user, body)
        return PlanRuntimeResponse(plan=plan)
    except (PlanningNotFound, PlanningConflict, PlanningPolicyError) as exc:
        _translate(exc)


@router.post("/plans/{plan_id}/interactions/{interaction_id}/skip", response_model=PlanRuntimeResponse, summary="跳过交互问题")
async def skip_interaction(
    plan_id: str,
    interaction_id: str,
    body: PlanApprovalRequest,
    session: DbSession,
    user: AuthenticatedUser,
) -> PlanRuntimeResponse:
    try:
        plan = await SqlPlanningStore(session).skip_interaction(
            plan_id, interaction_id, user, body.expected_revision
        )
        return PlanRuntimeResponse(plan=plan)
    except (PlanningNotFound, PlanningConflict, PlanningPolicyError) as exc:
        _translate(exc)


@router.post("/plans/{plan_id}/actions", response_model=PlanRuntimeResponse, summary="提交统一交互动作")
async def apply_action(
    plan_id: str,
    body: InteractionAction,
    session: DbSession,
    user: AuthenticatedUser,
) -> PlanRuntimeResponse:
    """统一交互动作协议入口（M2）。

    服务端校验归属、版本 CAS、幂等、动作白名单与目标定位后分发执行。
    """
    if body.plan_id != plan_id:
        raise PlanningPolicyErrorResponse("plan id does not match request path")
    try:
        plan, affected = await SqlPlanningStore(session).apply_action(plan_id, user, body)
        return PlanRuntimeResponse(plan=plan, affected=affected)
    except (PlanningNotFound, PlanningConflict, PlanningPolicyError) as exc:
        _translate(exc)


@router.post("/plans/{plan_id}/approve", response_model=PlanRuntimeResponse, summary="明确确认计划")
async def approve_plan(
    plan_id: str,
    body: PlanApprovalRequest,
    session: DbSession,
    user: AuthenticatedUser,
) -> PlanRuntimeResponse:
    try:
        plan = await SqlPlanningStore(session).approve(plan_id, user, body.expected_revision)
        return PlanRuntimeResponse(plan=plan)
    except (PlanningNotFound, PlanningConflict, PlanningPolicyError) as exc:
        _translate(exc)


@router.post("/plans/{plan_id}/switch-to-agent", response_model=PlanRuntimeResponse, summary="单独切换至 Agent 模式")
async def switch_to_agent(
    plan_id: str,
    body: ConversationModeUpdate,
    session: DbSession,
    user: AuthenticatedUser,
) -> PlanRuntimeResponse:
    if body.mode != "agent":
        raise PlanningPolicyErrorResponse("target mode must be agent")
    try:
        plan = await SqlPlanningStore(session).switch_mode(
            plan_id, user, body.expected_revision, body.mode
        )
        approved_revision = await SqlPlanningStore(session).get_approved_revision(plan_id, user)
        return PlanRuntimeResponse(plan=plan, approved_revision=approved_revision)
    except (PlanningNotFound, PlanningConflict, PlanningPolicyError) as exc:
        _translate(exc)


@router.post("/plans/{plan_id}/authorize-execution", response_model=PlanRuntimeResponse, summary="单独授权执行")
async def authorize_execution(
    plan_id: str,
    body: ExecutionAuthorizationRequest,
    session: DbSession,
    user: AuthenticatedUser,
    request: Request,
    runtime: AgentRuntime = Depends(get_agent_runtime),
    mcp: McpService | None = Depends(optional_mcp),
) -> PlanRuntimeResponse:
    if body.approved_plan_id != plan_id:
        raise PlanningPolicyErrorResponse("approved plan id does not match request path")
    try:
        plan = await SqlPlanningStore(session).authorize_execution(
            plan_id,
            user,
            approved_revision=body.approved_revision,
            expected_revision=body.expected_revision,
            authorization_id=body.authorization_id,
        )
        db_task_id = uuid.UUID(hex=uuid.uuid4().hex)
        task_id = db_task_id.hex
        execution_id = uuid.uuid4()
        run_request_id = get_request_id()
        conversation_id = uuid.UUID(plan.conversation_id)
        snapshot = plan.model_dump(mode="json")
        session.add(
            AgentTask(
                id=db_task_id,
                user_id=user.id,
                session_id=conversation_id,
                request_id=run_request_id,
                task_type="agent",
                status="created",
                input_payload={
                    "surface": "interactive_planning_handoff",
                    "plan_id": plan.plan_id,
                    "approved_revision": body.approved_revision,
                    "snapshot": snapshot,
                },
            )
        )
        session.add(
            TaskExecution(
                id=execution_id,
                task_id=db_task_id,
                status="pending",
                execution_input={"plan_id": plan.plan_id, "approved_revision": body.approved_revision},
            )
        )
        await session.commit()
        persistence = AgentRunPersistence(
            get_session_factory(),
            db_task_id,
            execution_id,
            lambda: runtime.get(task_id),
        )
        task_text = (
            f"Execute the user-approved plan: {plan.goal}\n\n"
            f"Confirmed decisions: {[item.model_dump(mode='json') for item in plan.decisions]}\n"
            f"Constraints: {plan.constraints}\n"
            f"Steps: {[item.model_dump(mode='json') for item in plan.steps]}"
        )
        agent_request = AgentRequest(
            message=task_text,
            prompt_name="assistant",
            grants=[],
            skill_mode=None,
        )
        mcp_user_id = await mcp.user_id_from_request(request) if mcp is not None else None
        try:
            run = await runtime.start(
                agent_request,
                run_request_id,
                workspace_scope=scope_dirname(request.headers.get("x-workspace-key")),
                mcp_user_id=mcp_user_id,
                mcp_service=mcp,
                task_id=task_id,
                event_observer=persistence,
            )
        except Exception:
            task_row = await session.get(AgentTask, db_task_id)
            execution_row = await session.get(TaskExecution, execution_id)
            if task_row is not None:
                task_row.status = TaskStatus.FAILED.value
                task_row.error_code = "AGENT_START_FAILED"
            if execution_row is not None:
                execution_row.status = "failed"
                execution_row.error_code = "AGENT_START_FAILED"
                execution_row.finished_at = datetime.now(UTC)
            await SqlPlanningStore(session)._transition(
                plan_id,
                user,
                plan.revision,
                PlanStatus.FAILED,
                PlanningEventType.RUN_FAILED,
                expected=PlanStatus.EXECUTING,
            )
            await session.commit()
            raise
        return PlanRuntimeResponse(plan=plan, approved_revision=body.approved_revision, run=run)
    except (PlanningNotFound, PlanningConflict, PlanningPolicyError) as exc:
        _translate(exc)


@router.get("/plans/{plan_id}/events", response_model=PlanEventList, summary="按序号恢复计划事件")
async def plan_events(
    plan_id: str,
    session: DbSession,
    user: AuthenticatedUser,
    after: int = Query(default=0, ge=0),
) -> PlanEventList:
    try:
        events, next_sequence = await SqlPlanningStore(session).events_after(plan_id, user, after)
        return PlanEventList(events=events, next_sequence=next_sequence)
    except (PlanningNotFound, PlanningConflict, PlanningPolicyError) as exc:
        _translate(exc)
