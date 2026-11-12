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
from app.core.context import get_request_id
from app.core.database.session import get_session_factory
from app.core.exceptions import (
    InvalidRequestError,
    LoginRequiredError,
    PlanningResourceNotFoundError,
    ToolRoundLimitError,
)
from app.services.auth_service import AuthService
from app.authorization.application.audit_service import QuotaService
from app.authorization.application.quota_bridge import record_tool_in_new_session, settle_in_new_session
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
from app.services.chat_service import ChatService, content_text
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
    conversation_title = (conversation.title or "")[:200]
    raw_user = next((item.get("content", "") for item in reversed(prepared.provider_request.messages) if item.get("role") == "user"), "")
    user_text = content_text(raw_user) or "[附件]"
    session.add(ConversationMessage(session_id=conversation.id, role="user", content=user_text))
    await store._emit_conversation(conversation.id, PlanningEventType.CHAT_RUN_STARTED, {"run_id": run_id})
    await session.commit()
    assistant_text = ""
    reasoning_text = ""
    tool_preview = False
    terminal_type = PlanningEventType.CHAT_CANCELLED
    terminal_payload: dict[str, object] = {"run_id": run_id}
    pending_durable_frames = 0
    usage_total = 0
    usage_prompt = 0
    usage_completion = 0
    usage_round = 0
    settled_total = 0
    settled_prompt = 0
    settled_completion = 0
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
                    reasoning = payload.get("reasoning_content")
                    if isinstance(reasoning, str):
                        reasoning_text += reasoning
                    await store._emit_conversation(
                        conversation.id,
                        PlanningEventType.CHAT_DELTA,
                        {"run_id": run_id, "sse_event": event_name, **payload},
                    )
                    pending_durable_frames += 1
                    if pending_durable_frames >= 8:
                        await session.commit()
                        pending_durable_frames = 0
                elif event_name == "usage":
                    usage_payload = payload.get("usage") or {}
                    tokens = usage_payload.get("total_tokens")
                    prompt = usage_payload.get("prompt_tokens")
                    completion = usage_payload.get("completion_tokens")
                    round_total = int(tokens) if isinstance(tokens, (int, float)) else 0
                    round_prompt = int(prompt) if isinstance(prompt, (int, float)) else 0
                    round_completion = int(completion) if isinstance(completion, (int, float)) else 0
                    usage_total += round_total
                    usage_prompt += round_prompt
                    usage_completion += round_completion
                    usage_round += 1
                    # 先记额度，再写会话。后面的落库失败不能把这一轮的真实用量丢掉。
                    settled = await _settle_chat_usage(
                        owner.id,
                        settlement_key=f"{run_id}:{usage_round}",
                        total_tokens=round_total,
                        prompt_tokens=round_prompt,
                        completion_tokens=round_completion,
                        model=prepared.provider_request.model,
                        conversation_id=conversation_id,
                        conversation_title=conversation_title,
                        meter=payload.get("meter") if isinstance(payload.get("meter"), dict) else None,
                    )
                    if settled is not None:
                        settled_total += round_total
                        settled_prompt += round_prompt
                        settled_completion += round_completion
                    await store._emit_conversation(conversation.id, PlanningEventType.CHAT_STATUS, {"run_id": run_id, **payload})
                    await session.commit()
                    pending_durable_frames = 0
                elif event_name == "resource" and payload.get("kind") == "tool":
                    await record_tool_in_new_session(
                        owner.id,
                        payload,
                        conversation_id=conversation_id,
                        task_id=run_id,
                    )
                elif event_name in {"tool", "status"}:
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
        if terminal_type == PlanningEventType.CHAT_DONE and (assistant_text or reasoning_text):
            session.add(
                ConversationMessage(
                    session_id=conversation.id,
                    role="assistant",
                    content=assistant_text,
                    metadata_json={"reasoning_content": reasoning_text} if reasoning_text else {},
                )
            )
        await store._emit_conversation(conversation.id, terminal_type, terminal_payload)
    except BaseException:
        await session.rollback()
        raise
    finally:
        if usage_total > settled_total:
            await _settle_chat_usage(
                owner.id,
                settlement_key=f"{run_id}:rest",
                total_tokens=usage_total - settled_total,
                prompt_tokens=max(0, usage_prompt - settled_prompt),
                completion_tokens=max(0, usage_completion - settled_completion),
                model=prepared.provider_request.model,
                conversation_id=conversation_id,
                conversation_title=conversation_title,
            )
        try:
            await session.commit()
        except Exception:
            logger.exception("chat transcript commit failed conversation_id=%s", conversation_id)
            await session.rollback()


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
    request: Request,
    service: ChatService = Depends(get_chat_service),
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_optional_bearer)] = None,
) -> ChatCompletionResponse:
    """使用服务端提示词和 DeepSeek Chat Completions。不执行工具。"""
    owner = await _authorize_model_user(request, credentials)
    result = await service.complete(body)
    if owner is not None:
        usage = result.usage
        purpose = body.usage_purpose if body.usage_purpose == "summary" else None
        await _settle_chat_usage(
            owner.id,
            settlement_key=result.request_id,
            total_tokens=usage.total_tokens if usage is not None else 0,
            prompt_tokens=usage.prompt_tokens if usage is not None else 0,
            completion_tokens=usage.completion_tokens if usage is not None else 0,
            model=result.model,
            source="summary" if purpose == "summary" else "chat_completion",
            conversation_id=_usage_conversation_id(body),
            conversation_title=body.conversation_title,
            purpose=purpose,
        )
    return result


def _interrupted_usage(exc: Exception) -> tuple[int, int, int] | None:
    """工具循环中断时，取出已经产生的 token。没有调用过模型则不结算。"""
    if isinstance(exc, ToolRoundLimitError):
        return exc.total_tokens, exc.prompt_tokens, exc.completion_tokens
    if getattr(exc, "quota_settle", False):
        return (
            int(getattr(exc, "quota_total_tokens", 0) or 0),
            int(getattr(exc, "quota_prompt_tokens", 0) or 0),
            int(getattr(exc, "quota_completion_tokens", 0) or 0),
        )
    return None


def _database_ready(request: Request) -> bool:
    return bool(getattr(request.app.state, "database_ready", False))


async def _authorize_model_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
):
    """数据库可用时，模型调用必须属于已登录且仍有额度的用户。"""
    if not _database_ready(request):
        return None
    if credentials is None or not credentials.credentials:
        raise LoginRequiredError()
    try:
        factory = get_session_factory()
    except RuntimeError:
        raise LoginRequiredError() from None
    session = factory()
    try:
        owner = await AuthService(session).user_for_token(credentials.credentials)
        if owner is None:
            raise LoginRequiredError()
        await QuotaService(session).authorize(owner.id)
        await session.commit()
        return owner
    finally:
        await session.close()


def _usage_conversation_id(body: ChatCompletionRequest) -> str | None:
    if body.conversation_id:
        return body.conversation_id
    chat_id = (body.history_chat_id or "").strip()
    if body.usage_purpose == "summary" and chat_id:
        return f"chat:{chat_id[:64]}"
    return None


async def _settle_chat_usage(
    user_id,
    *,
    settlement_key: str,
    total_tokens: int,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    model: str | None = None,
    conversation_id: str | None = None,
    conversation_title: str | None = None,
    purpose: str | None = None,
    source: str = "chat",
    meter: dict | None = None,
):
    from app.metering.context import take_meters

    extra: dict[str, object] = {}
    if conversation_title:
        extra["conversation_title"] = conversation_title
    if purpose:
        extra["purpose"] = purpose
    meters = take_meters()
    if meter is not None:
        meters.append(meter)
    return await settle_in_new_session(
        user_id,
        source=source,
        settlement_key=settlement_key,
        amount=max(0, total_tokens),
        kind="chat",
        model=model,
        conversation_id=conversation_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        extra=extra or None,
        meters=meters or None,
    )


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

    正常事件顺序：有正文的分片发 `event: delta`。每一轮结束时发一条 `event: usage`，
    只保留该轮最后一次用量。全部成功后发一条 `event: done`。`done` 与 `error` 不会同时出现。

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
        from app.authorization.application.authorization_service import AuthorizationService

        permission = "agent.execute" if body.mode == "agent" else "chat.access"
        try:
            await AuthorizationService(session).require_permission(owner.id, permission)
        except Exception:
            await session.close()
            raise
        try:
            await SqlPlanningStore(session)._conversation(body.conversation_id, owner.id)
        except Exception:
            await session.close()
            raise PlanningResourceNotFoundError() from None
        await QuotaService(session).authorize(owner.id)
        await session.commit()
    elif _database_ready(request):
        owner = await _authorize_model_user(request, credentials)

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
        usage_total = 0
        usage_prompt = 0
        usage_completion = 0
        from app.metering.context import add_meter

        try:
            async for frame in service.iter_sse(
                prepared,
                request.is_disconnected,
                scope_dirname(request.headers.get("x-workspace-key")),
            ):
                parsed = _stream_event(frame)
                if parsed and parsed[0] == "usage":
                    usage_payload = parsed[1].get("usage") or {}
                    tokens = usage_payload.get("total_tokens")
                    prompt = usage_payload.get("prompt_tokens")
                    completion = usage_payload.get("completion_tokens")
                    if isinstance(tokens, (int, float)):
                        usage_total += int(tokens)
                    if isinstance(prompt, (int, float)):
                        usage_prompt += int(prompt)
                    if isinstance(completion, (int, float)):
                        usage_completion += int(completion)
                    meter = parsed[1].get("meter")
                    if isinstance(meter, dict):
                        add_meter(meter)
                elif parsed and parsed[0] == "resource" and owner is not None and parsed[1].get("kind") == "tool":
                    await record_tool_in_new_session(
                        owner.id,
                        parsed[1],
                        conversation_id=body.conversation_id,
                        task_id=prepared.request_id,
                    )
                yield frame
        finally:
            if owner is not None:
                await _settle_chat_usage(
                    owner.id,
                    settlement_key=prepared.request_id,
                    total_tokens=usage_total,
                    prompt_tokens=usage_prompt,
                    completion_tokens=usage_completion,
                    model=prepared.provider_request.model,
                    conversation_id=body.conversation_id,
                )
            else:
                from app.metering.context import take_meters

                take_meters()

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
    request: Request,
    service: ChatService = Depends(get_chat_service),
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_optional_bearer)] = None,
) -> ChatCompletionResponse:
    """把服务端注册的工具 schema 交给模型，校验并执行后再回传结果。"""
    owner = await _authorize_model_user(request, credentials)
    try:
        result = await service.run_tool_loop(body)
    except Exception as exc:
        billed = _interrupted_usage(exc)
        if owner is not None and billed is not None:
            total_tokens, prompt_tokens, completion_tokens = billed
            await _settle_chat_usage(
                owner.id,
                settlement_key=get_request_id(),
                total_tokens=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                model=body.model,
                source="tool_call",
            )
        raise
    if owner is not None:
        usage = result.usage
        await _settle_chat_usage(
            owner.id,
            settlement_key=result.request_id,
            total_tokens=usage.total_tokens if usage is not None else 0,
            prompt_tokens=usage.prompt_tokens if usage is not None else 0,
            completion_tokens=usage.completion_tokens if usage is not None else 0,
            model=result.model,
            source="tool_call",
        )
    return result
