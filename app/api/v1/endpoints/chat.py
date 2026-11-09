"""对话 HTTP 接口。只做参数接收和响应返回。"""

from __future__ import annotations

import logging
import json
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_authenticated_user, get_chat_service
from app.api.v1.endpoints.mcp import optional_mcp
from app.mcp.service import McpService
from app.api.errors import documented_errors, stream_preflight_errors
from app.core.exceptions import InvalidRequestError
from app.core.database.session import get_session_factory
from app.core.exceptions import LoginRequiredError, PlanningResourceNotFoundError
from app.services.auth_service import AuthService
from app.models.tables import ConversationMessage, User
from app.planning.contracts import PlanningEventType
from app.planning.runtime import PlanningNotFound
from app.planning.sql_store import SqlPlanningStore
from app.chat.run_registry import chat_run_registry
from app.schemas.chat import (
    CHAT_COMPLETION_EXAMPLE,
    CHAT_COMPLETION_FULL_EXAMPLE,
    JSON_COMPLETION_EXAMPLE,
    STREAM_CHAT_EXAMPLE,
    STREAM_CHAT_FULL_EXAMPLE,
    TOOL_CALL_EXAMPLE,
    TOOL_CALL_FULL_EXAMPLE,
    ChatCompletionRequest,
    ChatCompletionResponse,
    StreamChatRequest,
    ToolCallRequest,
)
from app.services.chat_service import ChatService
from app.tools.workspace_scope import scope_dirname

logger = logging.getLogger("muzi_llm")
router = APIRouter(prefix="/chat", tags=["chat"])
_optional_bearer = HTTPBearer(auto_error=False)


def _stream_event(frame: str) -> tuple[str, dict[str, object]] | None:
    event_name = "message"
    data_lines: list[str] = []
    for line in frame.splitlines():
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
    if not data_lines:
        return None
    try:
        value = json.loads("\n".join(data_lines))
    except (TypeError, ValueError):
        return None
    return event_name, value if isinstance(value, dict) else {}


async def _persisted_stream(
    service: ChatService,
    prepared,
    request: Request,
    workspace_scope: str | None,
    session: AsyncSession,
    owner: User,
    conversation_id: str,
    run_id: str,
):
    store = SqlPlanningStore(session)
    try:
        conversation = await store._conversation(conversation_id, owner.id, for_update=True)
    except PlanningNotFound:
        raise PlanningResourceNotFoundError() from None
    user_text = next((item.get("content", "") for item in reversed(prepared.provider_request.messages) if item.get("role") == "user"), "")
    session.add(ConversationMessage(session_id=conversation.id, role="user", content=str(user_text)))
    await store._emit_conversation(conversation.id, PlanningEventType.CHAT_RUN_STARTED, {"run_id": run_id})
    await session.commit()
    assistant_text = ""
    tool_preview = False
    terminal_type = PlanningEventType.CHAT_CANCELLED
    terminal_payload: dict[str, object] = {"run_id": run_id}
    pending_durable_frames = 0
    try:
        async for frame in service.iter_sse(prepared, request.is_disconnected, workspace_scope):
            parsed = _stream_event(frame)
            if parsed:
                event_name, payload = parsed
                if event_name == "tool" and payload.get("phase") == "started":
                    tool_preview = True
                elif event_name == "status" and payload.get("phase") == "answering":
                    tool_preview = False
                elif event_name == "note":
                    rewind = payload.get("rewind")
                    if isinstance(rewind, int) and rewind > 0:
                        assistant_text = assistant_text[:-rewind] if rewind < len(assistant_text) else ""
                    await store._emit_conversation(
                        conversation.id,
                        PlanningEventType.CHAT_DELTA,
                        {"run_id": run_id, "sse_event": event_name, **payload},
                    )
                    pending_durable_frames += 1
                    if pending_durable_frames >= 8:
                        await session.commit()
                        pending_durable_frames = 0
                elif event_name == "delta" and not tool_preview:
                    content = payload.get("content")
                    if isinstance(content, str):
                        assistant_text += content
                    await store._emit_conversation(
                        conversation.id,
                        PlanningEventType.CHAT_DELTA,
                        {"run_id": run_id, "sse_event": event_name, **payload},
                    )
                    pending_durable_frames += 1
                    if pending_durable_frames >= 8:
                        await session.commit()
                        pending_durable_frames = 0
                elif event_name in {"tool", "status", "usage"}:
                    event_type = PlanningEventType.CHAT_TOOL if event_name == "tool" else PlanningEventType.CHAT_STATUS
                    await store._emit_conversation(conversation.id, event_type, {"run_id": run_id, **payload})
                    await session.commit()
                    pending_durable_frames = 0
                elif event_name == "done":
                    terminal_type = PlanningEventType.CHAT_DONE
                    terminal_payload = {"run_id": run_id, **payload}
                elif event_name == "error":
                    terminal_type = PlanningEventType.CHAT_ERROR
                    terminal_payload = {"run_id": run_id, **payload}
            yield frame
        if terminal_type == PlanningEventType.CHAT_DONE and assistant_text:
            session.add(ConversationMessage(session_id=conversation.id, role="assistant", content=assistant_text))
        await store._emit_conversation(conversation.id, terminal_type, terminal_payload)
        await session.commit()
    except BaseException:
        await store._emit_conversation(conversation.id, PlanningEventType.CHAT_CANCELLED, {"run_id": run_id})
        await session.commit()
        raise


@router.post(
    "/completions",
    response_model=ChatCompletionResponse,
    response_model_exclude_none=True,
    responses=documented_errors(),
    summary="非流式对话",
)
async def create_completion(
    body: Annotated[
        ChatCompletionRequest,
        Body(
            openapi_examples={
                "full": {
                    "summary": "完整字段",
                    "description": "展示全部请求字段，数值已填好，可直接执行。",
                    "value": CHAT_COMPLETION_FULL_EXAMPLE,
                },
                "chat": {
                    "summary": "最简请求",
                    "description": "只填写消息和提示词，其余字段使用服务端默认值。",
                    "value": CHAT_COMPLETION_EXAMPLE,
                },
                "json": {
                    "summary": "JSON 模式",
                    "description": "已填写 json_assistant 和必填变量 task。",
                    "value": JSON_COMPLETION_EXAMPLE,
                },
            }
        ),
    ],
    service: ChatService = Depends(get_chat_service),
) -> ChatCompletionResponse:
    """使用服务端提示词和 DeepSeek Chat Completions。不执行工具。"""
    return await service.complete(body)


@router.post(
    "/completions/stream",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": (
                "text/event-stream。OpenAPI 不能把流开始后的 error 事件表示成独立 HTTP 状态码；"
                "连接一旦返回 200，后续的上游失败只出现在 SSE 里。"
            ),
            "content": {"text/event-stream": {}},
        },
        **stream_preflight_errors(),
    },
    summary="流式对话",
)
async def stream_completion(
    body: Annotated[
        StreamChatRequest,
        Body(
            openapi_examples={
                "full": {
                    "summary": "完整字段",
                    "description": "展示全部请求字段，数值已填好，可直接执行。",
                    "value": STREAM_CHAT_FULL_EXAMPLE,
                },
                "stream": {
                    "summary": "最简请求",
                    "description": "只填写消息和提示词，其余字段使用服务端默认值。",
                    "value": STREAM_CHAT_EXAMPLE,
                },
            }
        ),
    ],
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_optional_bearer)],
    service: ChatService = Depends(get_chat_service),
    mcp: McpService | None = Depends(optional_mcp),
) -> StreamingResponse:
    """与非流式接口共用提示词、模型和参数整理。

    正常事件顺序：有正文的分片发 `event: delta`；分片带 usage 时先发 `event: usage`，
    再发该分片的 `event: delta`。全部成功后发一条 `event: done`。`done` 与 `error` 不会同时出现。

    流开始前的校验或配置错误返回 JSON，状态码为 400、404、422 或 500。
    流开始后的上游超时、认证失败、限流、5xx 和无法解析的响应使用 `event: error`，HTTP 状态保持 200。
    客户端断开时不再发送 `done` 或 `error`，并关闭上游流。
    """
    if body.stream is False:
        raise InvalidRequestError("该接口只提供流式输出")
    remote: list = []
    user_id = await mcp.user_id_from_request(request) if mcp is not None else None
    if body.mode == "agent" and mcp is not None and user_id is not None:
        try:
            remote = await mcp.tool_specs(user_id)
        except Exception:
            logger.info("remote mcp tools skipped")
            remote = []
        from app.mcp.grants import sync_stdio

        await sync_stdio(user_id, request.app.state.settings.mcp_server_name)
    prepared = service.prepare_stream(body, remote, user_id)
    service.ensure_ready()
    owner = None
    session: AsyncSession | None = None
    if body.conversation_id:
        if credentials is None:
            raise LoginRequiredError()
        try:
            factory = get_session_factory()
        except RuntimeError:
            raise LoginRequiredError() from None
        session = factory()
        owner = await AuthService(session).user_for_token(credentials.credentials)
        if owner is None:
            await session.close()
            raise LoginRequiredError()
        try:
            await SqlPlanningStore(session)._conversation(body.conversation_id, owner.id)
        except Exception:
            await session.close()
            raise PlanningResourceNotFoundError() from None

    async def stream_body():
        if body.conversation_id and owner is not None:
            run_id = body.client_run_id or prepared.request_id
            await chat_run_registry.register(run_id, str(owner.id))
            try:
                assert session is not None
                async for frame in _persisted_stream(
                    service,
                    prepared,
                    request,
                    scope_dirname(request.headers.get("x-workspace-key")),
                    session,
                    owner,
                    body.conversation_id,
                    run_id,
                ):
                    yield frame
            finally:
                await chat_run_registry.unregister(run_id)
                if session is not None:
                    await session.close()
            return
        async for frame in service.iter_sse(
            prepared,
            request.is_disconnected,
            scope_dirname(request.headers.get("x-workspace-key")),
        ):
            yield frame

    return StreamingResponse(
        stream_body(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/runs/{run_id}/cancel", summary="取消 Chat 运行")
async def cancel_chat_run(
    run_id: str,
    user: Annotated[User, Depends(get_authenticated_user)],
) -> dict[str, object]:
    cancelled = await chat_run_registry.cancel(run_id, str(user.id))
    return {"run_id": run_id, "cancelled": cancelled}


@router.post(
    "/tool-calls",
    response_model=ChatCompletionResponse,
    response_model_exclude_none=True,
    responses=documented_errors(),
    summary="受控工具调用",
)
async def create_tool_call(
    body: Annotated[
        ToolCallRequest,
        Body(
            openapi_examples={
                "full": {
                    "summary": "完整字段",
                    "description": "展示全部请求字段，数值已填好，可直接执行。",
                    "value": TOOL_CALL_FULL_EXAMPLE,
                },
                "calculator": {
                    "summary": "最简请求",
                    "description": "只保留计算 23 × 19 所需字段。",
                    "value": TOOL_CALL_EXAMPLE,
                },
            }
        ),
    ],
    service: ChatService = Depends(get_chat_service),
) -> ChatCompletionResponse:
    """把服务端注册的工具 schema 交给模型，校验并执行后再回传结果。"""
    return await service.run_tool_loop(body)
