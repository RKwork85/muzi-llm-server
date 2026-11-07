"""对话 HTTP 接口。只做参数接收和响应返回。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.deps import get_chat_service
from app.api.errors import documented_errors, stream_preflight_errors
from app.core.exceptions import InvalidRequestError
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

router = APIRouter(prefix="/chat", tags=["chat"])


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
    service: ChatService = Depends(get_chat_service),
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
    prepared = service.prepare_stream(body)
    service.ensure_ready()
    return StreamingResponse(
        service.iter_sse(
            prepared,
            request.is_disconnected,
            scope_dirname(request.headers.get("x-workspace-key")),
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


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
