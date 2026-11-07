"""对话编排：提示词、模型白名单、补全和受限工具循环。"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.core.config import Settings
from app.core.context import get_request_id
from app.core.exceptions import (
    AppError,
    InternalError,
    InvalidModelResponseError,
    InvalidRequestError,
    ModelNotAllowedError,
    ToolRoundLimitError,
)
from app.core.sse import format_sse
from app.providers.base import CompletionResult, LLMProvider, ProviderChatRequest
from app.schemas.chat import (
    AssistantMessageOut,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChoiceOut,
    StreamChatRequest,
    ToolCallRequest,
)
from app.schemas.common import Usage
from app.services.prompt_service import PromptService
from app.services.tool_service import ToolService

logger = logging.getLogger("qs_llm")
ChatBody = ChatCompletionRequest | StreamChatRequest | ToolCallRequest


@dataclass
class PreparedChat:
    request_id: str
    provider_request: ProviderChatRequest
    expect_json: bool


class ChatService:
    def __init__(
        self,
        settings: Settings,
        provider: LLMProvider,
        prompts: PromptService,
        tools: ToolService,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.prompts = prompts
        self.tools = tools

    async def complete(self, body: ChatCompletionRequest) -> ChatCompletionResponse:
        if body.stream:
            raise InvalidRequestError("流式输出请调用 /api/v1/chat/completions/stream")
        prepared = self.prepare_chat(body)
        started = time.perf_counter()
        result = await self.provider.create_chat_completion(prepared.provider_request)
        self._ensure_result(result, prepared.expect_json)
        self._log("chat", prepared.request_id, started, result, None)
        return self._to_response(prepared, result, tool_rounds=None)

    async def run_tool_loop(self, body: ToolCallRequest) -> ChatCompletionResponse:
        schemas = self.tools.schemas_for(body.tool_names)
        max_rounds = self._resolve_rounds(body.max_tool_rounds)
        prepared = self.prepare_chat(
            body,
            tools=schemas,
            tool_choice="auto" if schemas else None,
        )
        messages = [dict(item) for item in prepared.provider_request.messages]
        rounds = 0
        started = time.perf_counter()
        while True:
            request = ProviderChatRequest(
                model=prepared.provider_request.model,
                messages=messages,
                temperature=prepared.provider_request.temperature,
                max_tokens=prepared.provider_request.max_tokens,
                thinking=prepared.provider_request.thinking,
                reasoning_effort=prepared.provider_request.reasoning_effort,
                response_format=None,
                tools=prepared.provider_request.tools,
                tool_choice=prepared.provider_request.tool_choice,
            )
            result = await self.provider.create_chat_completion(request)
            if not result.tool_calls:
                self._ensure_result(result, expect_json=False)
                self._log("tool_loop", prepared.request_id, started, result, rounds)
                return self._to_response(prepared, result, tool_rounds=rounds)
            rounds += 1
            if rounds > max_rounds:
                logger.info(
                    "tool round limit request_id=%s max_tool_rounds=%s",
                    prepared.request_id,
                    max_rounds,
                )
                raise ToolRoundLimitError()
            logger.info(
                "tool round request_id=%s round=%s tools=%s",
                prepared.request_id,
                rounds,
                ",".join(call.name for call in result.tool_calls),
            )
            messages.append(_assistant_message(result))
            messages.extend(await self.tools.execute(result.tool_calls))

    def ensure_ready(self) -> None:
        ensure = getattr(self.provider, "ensure_ready", None)
        if ensure is not None:
            ensure()

    def prepare_chat(
        self,
        body: ChatBody,
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
    ) -> PreparedChat:
        model = self._resolve_model(body.model)
        system_prompt = self.prompts.render(body.prompt_name, body.prompt_variables)
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for item in body.messages:
            if item.role not in {"user", "assistant"}:
                raise InvalidRequestError()
            messages.append({"role": item.role, "content": item.content})
        response_format = getattr(body, "response_format", None)
        expect_json = bool(response_format and response_format.type == "json_object")
        if expect_json:
            blob = "\n".join(str(message.get("content") or "") for message in messages)
            if "json" not in blob.lower():
                raise InvalidRequestError("JSON 模式要求系统提示词或用户消息包含 json 及格式示例")
        thinking_type = body.thinking.type if body.thinking else self.settings.deepseek_thinking
        provider_request = ProviderChatRequest(
            model=model,
            messages=messages,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
            thinking={"type": thinking_type},
            reasoning_effort=body.reasoning_effort,
            response_format={"type": "json_object"} if expect_json else None,
            tools=tools,
            tool_choice=tool_choice,
        )
        return PreparedChat(
            request_id=get_request_id(),
            provider_request=provider_request,
            expect_json=expect_json,
        )

    async def iter_sse(
        self,
        prepared: PreparedChat,
        is_disconnected: Callable[[], Awaitable[bool]],
    ) -> AsyncIterator[str]:
        stream = self.provider.stream_chat_completion(prepared.provider_request)
        started = time.perf_counter()
        last_id = ""
        last_model = prepared.provider_request.model
        last_finish = "stop"
        parts: list[str] = []
        try:
            async for chunk in stream:
                if await is_disconnected():
                    logger.info("stream client disconnected request_id=%s", prepared.request_id)
                    return
                if chunk.id:
                    last_id = chunk.id
                if chunk.model:
                    last_model = chunk.model
                if chunk.finish_reason:
                    last_finish = chunk.finish_reason
                if chunk.usage is not None:
                    yield format_sse(
                        "usage",
                        {
                            "request_id": prepared.request_id,
                            "usage": {
                                "prompt_tokens": chunk.usage.prompt_tokens,
                                "completion_tokens": chunk.usage.completion_tokens,
                                "total_tokens": chunk.usage.total_tokens,
                            },
                        },
                    )
                delta: dict[str, str] = {}
                if chunk.content:
                    parts.append(chunk.content)
                    delta["content"] = chunk.content
                if self.settings.expose_reasoning and chunk.reasoning_content:
                    delta["reasoning_content"] = chunk.reasoning_content
                if delta:
                    yield format_sse("delta", {"request_id": prepared.request_id, **delta})
            if prepared.expect_json:
                _validate_json_text("".join(parts), last_finish)
            self._log_stream(prepared.request_id, started, last_model)
            yield format_sse(
                "done",
                {
                    "request_id": prepared.request_id,
                    "id": last_id,
                    "model": last_model,
                    "finish_reason": last_finish,
                },
            )
        except AppError as exc:
            yield _sse_error(prepared.request_id, exc)
        except Exception:
            logger.error("stream failed request_id=%s", prepared.request_id)
            yield _sse_error(prepared.request_id, InternalError())
        finally:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                await aclose()

    def _resolve_model(self, requested: str | None) -> str:
        if requested is None or not requested.strip():
            return self.settings.deepseek_model
        name = requested.strip()
        if name not in self.settings.model_allowlist():
            raise ModelNotAllowedError()
        return name

    def _resolve_rounds(self, requested: int | None) -> int:
        value = self.settings.max_tool_rounds if requested is None else requested
        return min(value, self.settings.tool_round_hard_limit)

    def _ensure_result(self, result: CompletionResult, expect_json: bool) -> None:
        if expect_json:
            _validate_json_text(result.content or "", result.finish_reason)
            return
        if result.tool_calls:
            return
        if not (result.content and result.content.strip()):
            raise InvalidModelResponseError()

    def _to_response(
        self,
        prepared: PreparedChat,
        result: CompletionResult,
        tool_rounds: int | None,
    ) -> ChatCompletionResponse:
        reasoning = result.reasoning_content if self.settings.expose_reasoning else None
        usage = None
        if result.usage is not None:
            usage = Usage(
                prompt_tokens=result.usage.prompt_tokens,
                completion_tokens=result.usage.completion_tokens,
                total_tokens=result.usage.total_tokens,
            )
        return ChatCompletionResponse(
            request_id=prepared.request_id,
            id=result.id or f"local-{uuid4().hex}",
            model=result.model or prepared.provider_request.model,
            choices=[
                ChoiceOut(
                    index=0,
                    message=AssistantMessageOut(content=result.content, reasoning_content=reasoning),
                    finish_reason=result.finish_reason,
                )
            ],
            usage=usage,
            finish_reason=result.finish_reason,
            tool_rounds=tool_rounds,
        )

    def _log(
        self,
        kind: str,
        request_id: str,
        started: float,
        result: CompletionResult,
        tool_rounds: int | None,
    ) -> None:
        usage = result.usage
        logger.info(
            "%s completed request_id=%s model=%s latency_ms=%.1f prompt_tokens=%s completion_tokens=%s total_tokens=%s tool_rounds=%s",
            kind,
            request_id,
            result.model,
            (time.perf_counter() - started) * 1000,
            usage.prompt_tokens if usage else None,
            usage.completion_tokens if usage else None,
            usage.total_tokens if usage else None,
            tool_rounds,
        )

    def _log_stream(self, request_id: str, started: float, model: str) -> None:
        logger.info(
            "stream completed request_id=%s model=%s latency_ms=%.1f",
            request_id,
            model,
            (time.perf_counter() - started) * 1000,
        )


def _assistant_message(result: CompletionResult) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": result.content or "",
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in result.tool_calls
        ],
    }
    if result.reasoning_content is not None:
        message["reasoning_content"] = result.reasoning_content
    return message


def _validate_json_text(content: str, finish_reason: str) -> None:
    if finish_reason == "length" or not content.strip():
        raise InvalidModelResponseError()
    try:
        json.loads(content)
    except json.JSONDecodeError:
        raise InvalidModelResponseError() from None


def _sse_error(request_id: str, exc: AppError) -> str:
    return format_sse(
        "error",
        {
            "request_id": request_id,
            "error": {"code": exc.code, "message": exc.message},
        },
    )
