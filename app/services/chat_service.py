"""对话编排：提示词、模型白名单、补全和受限工具循环。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.agents.toolsets import ask_tool_names
from app.core.config import Settings
from app.core.context import get_request_id
from app.core.exceptions import (
    AppError,
    InternalError,
    InvalidModelResponseError,
    InvalidRequestError,
    ModelNotAllowedError,
    ToolRoundLimitError,
    UpstreamTimeoutError,
)
from app.core.sse import format_sse
from app.providers.base import CompletionResult, LLMProvider, ProviderChatRequest, ToolCall
from app.schemas.chat import (
    AssistantMessageOut,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChoiceOut,
    StreamChatRequest,
    ToolCallRequest,
)
from app.schemas.common import Usage
from app.data.context import CURRENT_DATA_STORE, CURRENT_OSS_CLIENT, CURRENT_TASK_ID
from app.services.prompt_service import PromptService
from app.services.tool_service import ToolService
from app.tools.deliver_file import USER_DOWNLOAD_KIND
from app.tools.file_sandbox import FileDenied
from app.tools.file_tools import workspace_root
from app.tools.url_policy import UrlLedger
from app.tools.web_tools import CURRENT_SETTINGS, CURRENT_URL_LEDGER, CURRENT_WEB_CLIENT, UNTRUSTED_PROMPT
from app.tools.workspace_scope import CURRENT_WORKSPACE_SCOPE, is_user_scope

logger = logging.getLogger("qs_llm")
ChatBody = ChatCompletionRequest | StreamChatRequest | ToolCallRequest


@dataclass
class PreparedChat:
    request_id: str
    provider_request: ProviderChatRequest
    expect_json: bool
    max_tool_rounds: int = 0


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
        self.ask_tools: ToolService | None = None
        self.web_client: Any | None = None
        self.data_store: Any | None = None
        self.oss_client: Any | None = None

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

    def prepare_stream(self, body: StreamChatRequest) -> PreparedChat:
        """Ask：服务端挂上短工具，模型自己决定是否调用。页面不能扩大工具集合。"""
        names = self._ask_names()
        use_tools = bool(names) and not _wants_json(body)
        tools = self.ask_tools.schemas_for(list(names)) if use_tools and self.ask_tools else None
        prepared = self.prepare_chat(
            body,
            tools=tools,
            tool_choice="auto" if tools else None,
        )
        prepared.max_tool_rounds = self._resolve_rounds(None)
        if use_tools and self._ask_needs_untrusted(names):
            system = prepared.provider_request.messages[0]
            system["content"] = f"{system['content']}\n\n{UNTRUSTED_PROMPT}"
        return prepared

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
        workspace_scope: str | None = None,
    ) -> AsyncIterator[str]:
        if prepared.provider_request.tools:
            async for frame in self._iter_ask_sse(prepared, is_disconnected, workspace_scope):
                yield frame
            return
        async for frame in self._iter_plain_sse(prepared, is_disconnected, prepared.provider_request):
            yield frame

    async def _iter_plain_sse(
        self,
        prepared: PreparedChat,
        is_disconnected: Callable[[], Awaitable[bool]],
        request: ProviderChatRequest,
    ) -> AsyncIterator[str]:
        stream = self.provider.stream_chat_completion(request)
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

    async def _iter_ask_sse(
        self,
        prepared: PreparedChat,
        is_disconnected: Callable[[], Awaitable[bool]],
        workspace_scope: str | None = None,
    ) -> AsyncIterator[str]:
        messages = [dict(item) for item in prepared.provider_request.messages]
        rounds = 0
        ledger_text = "\n".join(
            str(item.get("content") or "") for item in messages if item.get("role") == "user"
        )
        tokens = self._enter_ask_context(
            ledger_text or "ask",
            prepared.request_id,
            workspace_scope,
        )
        try:
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
                started = time.perf_counter()
                slots: dict[int, dict[str, str]] = {}
                parts: list[str] = []
                reasoning_parts: list[str] = []
                last_id = ""
                last_model = request.model
                last_finish = "stop"
                saw_tools = False
                announced: set[int] = set()
                previews = _WritePreview()
                pending_answer: list[str] = []
                stream = self.provider.stream_chat_completion(request)
                iterator = stream.__aiter__()
                try:
                    while True:
                        try:
                            chunk = await asyncio.wait_for(
                                iterator.__anext__(),
                                self.settings.deepseek_timeout_seconds,
                            )
                        except StopAsyncIteration:
                            break
                        except TimeoutError:
                            raise UpstreamTimeoutError() from None
                        if await is_disconnected():
                            logger.info("stream client disconnected request_id=%s", prepared.request_id)
                            return
                        if chunk.id:
                            last_id = chunk.id
                        if chunk.model:
                            last_model = chunk.model
                        if chunk.finish_reason:
                            last_finish = chunk.finish_reason
                        if chunk.reasoning_content:
                            reasoning_parts.append(chunk.reasoning_content)
                        consumed_content = False
                        if chunk.tool_call_parts and not saw_tools:
                            lead = "".join(pending_answer)
                            pending_answer.clear()
                            streamed = len(lead)
                            if chunk.content:
                                lead += chunk.content
                                parts.append(chunk.content)
                                consumed_content = True
                            if lead:
                                yield format_sse(
                                    "note",
                                    {
                                        "request_id": prepared.request_id,
                                        "round": rounds + 1,
                                        "content": lead,
                                        "rewind": streamed,
                                    },
                                )
                            saw_tools = True
                        elif chunk.tool_call_parts:
                            saw_tools = True
                        if chunk.tool_call_parts:
                            _absorb_tool_parts(slots, chunk.tool_call_parts)
                            for index, _call_id, _name, _arguments in chunk.tool_call_parts:
                                slot = slots.get(index)
                                if slot is None or not slot["name"]:
                                    continue
                                if index not in announced:
                                    announced.add(index)
                                    yield format_sse(
                                        "tool",
                                        {
                                            "request_id": prepared.request_id,
                                            "name": slot["name"],
                                            "round": rounds + 1,
                                            "index": index,
                                            "phase": "started",
                                        },
                                    )
                                piece = previews.push(index, slot["name"], slot["arguments"])
                                if piece:
                                    yield format_sse(
                                        "delta",
                                        {"request_id": prepared.request_id, "content": piece},
                                    )
                        if saw_tools:
                            if chunk.content and not consumed_content:
                                parts.append(chunk.content)
                                yield format_sse(
                                    "note",
                                    {
                                        "request_id": prepared.request_id,
                                        "round": rounds + 1,
                                        "content": chunk.content,
                                    },
                                )
                            continue
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
                            pending_answer.append(chunk.content)
                            parts.append(chunk.content)
                            delta["content"] = chunk.content
                        if self.settings.expose_reasoning and chunk.reasoning_content:
                            delta["reasoning_content"] = chunk.reasoning_content
                        if delta:
                            yield format_sse("delta", {"request_id": prepared.request_id, **delta})
                except AppError as exc:
                    yield _sse_error(prepared.request_id, exc)
                    return
                except Exception:
                    logger.error("stream failed request_id=%s", prepared.request_id)
                    yield _sse_error(prepared.request_id, InternalError())
                    return
                finally:
                    aclose = getattr(stream, "aclose", None)
                    if aclose is not None:
                        await aclose()
                if not saw_tools:
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
                    return
                call_indexes = sorted(slots)
                calls = _calls_from_slots(slots)
                rounds += 1
                if rounds > prepared.max_tool_rounds:
                    logger.info(
                        "tool round limit request_id=%s max_tool_rounds=%s",
                        prepared.request_id,
                        prepared.max_tool_rounds,
                    )
                    yield _sse_error(prepared.request_id, ToolRoundLimitError())
                    return
                logger.info(
                    "tool round request_id=%s round=%s tools=%s",
                    prepared.request_id,
                    rounds,
                    ",".join(call.name for call in calls),
                )
                messages.append(
                    _assistant_message(
                        CompletionResult(
                            id=last_id,
                            model=last_model,
                            content="".join(parts) or None,
                            reasoning_content="".join(reasoning_parts) or None,
                            tool_calls=calls,
                            finish_reason=last_finish or "tool_calls",
                        )
                    )
                )
                if self.ask_tools is None:
                    yield _sse_error(prepared.request_id, InternalError())
                    return
                tool_messages = await self.ask_tools.execute(
                    calls, allowed=frozenset(self._ask_names())
                )
                messages.extend(tool_messages)
                for slot_index, call, tool_message in zip(call_indexes, calls, tool_messages, strict=True):
                    detail, preview = _tool_public(call.name, call.arguments, tool_message["content"])
                    cards, total = _tool_cards(call.name, call.arguments, tool_message["content"])
                    payload: dict[str, Any] = {
                        "request_id": prepared.request_id,
                        "name": call.name,
                        "round": rounds,
                        "index": slot_index,
                        "phase": "done",
                        "detail": detail,
                        "preview": preview,
                    }
                    if cards:
                        payload["items"] = cards
                        payload["count"] = total
                    diagram = _diagram_source(tool_message["content"])
                    if diagram:
                        payload["diagram"] = diagram
                    yield format_sse("tool", payload)
                for item in _download_files(tool_messages):
                    yield format_sse("file", {"request_id": prepared.request_id, **item})
                yield format_sse(
                    "status",
                    {"request_id": prepared.request_id, "phase": "answering"},
                )
        except AppError as exc:
            yield _sse_error(prepared.request_id, exc)
        except Exception:
            logger.error("ask stream failed request_id=%s", prepared.request_id)
            yield _sse_error(prepared.request_id, InternalError())
        finally:
            for var, token in reversed(tokens):
                var.reset(token)

    def _ask_names(self) -> tuple[str, ...]:
        if self.ask_tools is None:
            return ()
        return ask_tool_names(self.ask_tools.registry, self.settings)

    def _ask_needs_untrusted(self, names: tuple[str, ...]) -> bool:
        if self.ask_tools is None:
            return False
        for name in names:
            spec = self.ask_tools.registry.get(name)
            if spec is not None and spec.toolset != "basic":
                return True
        return False

    def _enter_ask_context(
        self,
        message: str,
        request_id: str,
        workspace_scope: str | None = None,
    ) -> list[tuple[Any, Any]]:
        tokens: list[tuple[Any, Any]] = [
            (CURRENT_SETTINGS, CURRENT_SETTINGS.set(self.settings)),
            (CURRENT_URL_LEDGER, CURRENT_URL_LEDGER.set(UrlLedger(message))),
        ]
        if workspace_scope is not None:
            tokens.append(
                (CURRENT_WORKSPACE_SCOPE, CURRENT_WORKSPACE_SCOPE.set(workspace_scope))
            )
            self._prepare_workspace(request_id, workspace_scope)
        if self.web_client is not None:
            tokens.append((CURRENT_WEB_CLIENT, CURRENT_WEB_CLIENT.set(self.web_client)))
        if self.data_store is not None:
            tokens.append((CURRENT_DATA_STORE, CURRENT_DATA_STORE.set(self.data_store)))
            tokens.append((CURRENT_TASK_ID, CURRENT_TASK_ID.set(request_id)))
        if self.oss_client is not None:
            tokens.append((CURRENT_OSS_CLIENT, CURRENT_OSS_CLIENT.set(self.oss_client)))
        return tokens

    def _prepare_workspace(self, request_id: str, workspace_scope: str) -> None:
        if is_user_scope(workspace_scope):
            logger.info("workspace bound request_id=%s scope=%s", request_id, workspace_scope)
        else:
            logger.info("workspace unbound request_id=%s", request_id)
        try:
            workspace_root(self.settings)
        except FileDenied:
            return

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


def _download_files(tool_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从工具结果里取出带 user_download 标识的文件，供前端渲染下载入口。"""
    found: list[dict[str, Any]] = []
    for message in tool_messages:
        payload = _download_payload(message.get("content"))
        if payload is None:
            continue
        found.append(payload)
    return found


def _download_payload(content: Any) -> dict[str, Any] | None:
    if not isinstance(content, str):
        return None
    try:
        wrapped = json.loads(content)
    except json.JSONDecodeError:
        return None
    raw = wrapped.get("result") if isinstance(wrapped, dict) else None
    if not isinstance(raw, str):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("kind") != USER_DOWNLOAD_KIND:
        return None
    url = payload.get("download_url")
    name = payload.get("file_name")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None
    if not isinstance(name, str) or not name.strip():
        return None
    size = payload.get("file_size")
    return {
        "kind": USER_DOWNLOAD_KIND,
        "file_name": name.strip()[:120],
        "download_url": url,
        "file_size": size if isinstance(size, int) and size >= 0 else None,
    }


def _wants_json(body: StreamChatRequest) -> bool:
    response_format = body.response_format
    return bool(response_format and response_format.type == "json_object")


_DETAIL_KEY = {
    "web_search": "query",
    "web_fetch": "url",
    "weather_query": "place",
    "calculator": "expression",
    "write_text_file": "path",
    "write_docx": "path",
    "create_flowchart": "title",
    "create_sequence_diagram": "title",
    "read_text_file": "path",
    "deliver_file": "path",
    "delete_file": "path",
    "search_text": "query",
    "list_directory": "path",
}
_PREVIEW_LIMIT = 500


def _clip(value: str) -> str:
    text = str(value).strip()
    if len(text) <= _PREVIEW_LIMIT:
        return text
    return text[:_PREVIEW_LIMIT] + "…"


def _argument_detail(name: str, arguments: str) -> str:
    key = _DETAIL_KEY.get(name)
    if not key:
        return ""
    try:
        payload = json.loads(arguments or "")
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or value.strip() == ".":
        return ""
    return _clip(value.strip())


def _result_preview(content: str) -> str:
    payload = _tool_payload(content)
    if not isinstance(payload, dict):
        raw = content
        try:
            wrapped = json.loads(content)
        except json.JSONDecodeError:
            wrapped = None
        if isinstance(wrapped, dict) and isinstance(wrapped.get("result"), str):
            raw = wrapped["result"]
        return _clip(raw)
    error = payload.get("error")
    if isinstance(error, dict) and error.get("message"):
        return _clip(str(error["message"]))
    if "temperature_c" in payload:
        return _clip(
            f"{payload.get('place') or ''} {payload.get('condition') or ''} {payload.get('temperature_c')}°C".strip()
        )
    results = payload.get("results")
    if isinstance(results, list):
        lines: list[str] = []
        for item in results[:3]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            summary = str(item.get("summary") or "").strip()
            if title and summary:
                lines.append(f"{title}：{summary[:160]}")
            elif title:
                lines.append(title)
        if lines:
            return _clip("\n".join(lines))
    title = payload.get("title")
    body = payload.get("untrusted_content")
    if isinstance(title, str) or isinstance(body, str):
        return _clip(f"{title or ''}\n{str(body or '')[:240]}".strip())
    if isinstance(payload.get("mermaid"), str):
        return ""
    if isinstance(payload.get("file_name"), str):
        return _clip(str(payload["file_name"]))
    if isinstance(payload.get("source"), str) and "bytes" in payload:
        return _clip(f"{payload['source']} · {payload['bytes']} 字节")
    return ""


def _tool_public(name: str, arguments: str, content: str) -> tuple[str, str]:
    return _argument_detail(name, arguments), _result_preview(content)


def _diagram_source(content: str) -> str:
    """流程图和序列图的源码交给页面直接画，不依赖模型再贴一次。"""
    payload = _tool_payload(content)
    if not isinstance(payload, dict):
        return ""
    source = payload.get("mermaid")
    if not isinstance(source, str):
        return ""
    text = source.strip()
    if not text.startswith(("flowchart ", "graph ", "sequenceDiagram")):
        return ""
    if len(text) > 8000 or "<script" in text.lower() or "javascript:" in text.lower():
        return ""
    return text


_TRUNCATION_MARK = "...[truncated]"
_QUOTED_FIELD = re.compile(r'(?<!\\)"(?P<key>[A-Za-z0-9_]+)"\s*:\s*"((?:\\.|[^"\\])*)"')


def _tool_payload(content: str) -> dict[str, Any] | None:
    try:
        wrapped = json.loads(content)
    except json.JSONDecodeError:
        return None
    if isinstance(wrapped, dict) and isinstance(wrapped.get("result"), str):
        wrapped = _loads_result_json(wrapped["result"])
    return wrapped if isinstance(wrapped, dict) else None


def _loads_result_json(raw: str) -> Any | None:
    """工具结果超长时会被截断，截断点之前的完整字段仍然要能读出来。"""
    text = raw[: -len(_TRUNCATION_MARK)] if raw.endswith(_TRUNCATION_MARK) else raw
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        repaired = _loads_truncated_json(text)
        if isinstance(repaired, dict):
            _keep_complete_search_hits(repaired, text)
        return repaired


def _quoted_field(source: str, key: str) -> list[str]:
    found: list[str] = []
    for match in _QUOTED_FIELD.finditer(source):
        if match.group("key") != key:
            continue
        try:
            found.append(json.loads(f'"{match.group(2)}"'))
        except json.JSONDecodeError:
            continue
    return found


def _keep_complete_search_hits(payload: dict[str, Any], source: str) -> None:
    urls = [url for url in _quoted_field(source, "url") if url.startswith(("http://", "https://"))]
    results = payload.get("results")
    if not isinstance(results, list) and not urls:
        return
    allowed = set(urls)
    kept: list[dict[str, Any]] = []
    if isinstance(results, list):
        for item in results:
            if isinstance(item, dict) and isinstance(item.get("url"), str) and item["url"] in allowed:
                kept.append(item)
    if not kept and urls:
        titles = _quoted_field(source, "title")
        kept = [
            {"title": titles[index] if index < len(titles) else url, "url": url}
            for index, url in enumerate(urls)
        ]
    payload["results"] = kept


def _loads_truncated_json(text: str) -> Any | None:
    candidate = text.rstrip()
    for _ in range(6):
        closed = re.sub(r",(\s*[}\]])", r"\1", _close_json(candidate))
        try:
            return json.loads(closed)
        except json.JSONDecodeError:
            trimmed = _trim_incomplete_tail(candidate)
            if not trimmed or trimmed == candidate:
                return None
            candidate = trimmed
    return None


def _close_json(text: str) -> str:
    stack: list[str] = []
    in_string = False
    escape = False
    for char in text:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            stack.append("}")
        elif char == "[":
            stack.append("]")
        elif char in "}]" and stack and stack[-1] == char:
            stack.pop()
    if escape and text:
        text = text[:-1]
    suffix = '"' if in_string else ""
    return text + suffix + "".join(reversed(stack))


def _trim_incomplete_tail(text: str) -> str:
    in_string = False
    escape = False
    last = -1
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in ",{[":
            last = index
    if last < 0:
        return text
    if text[last] == ",":
        return text[:last]
    return text[: last + 1]


def _card_field(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()[:limit]


def _tool_cards(name: str, arguments: str, content: str) -> tuple[list[dict[str, str]], int]:
    """给前端的标题、链接和短摘要。不带网页正文。"""
    payload = _tool_payload(content)
    if not isinstance(payload, dict) or payload.get("error"):
        return [], 0
    if name == "web_search":
        results = payload.get("results")
        if not isinstance(results, list):
            return [], 0
        items: list[dict[str, str]] = []
        total = 0
        for item in results:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            total += 1
            if len(items) >= 12:
                continue
            title = item.get("title")
            label = title.strip()[:80] if isinstance(title, str) and title.strip() else url[:80]
            card = {"title": label, "url": url[:2048]}
            site = _card_field(item.get("site_name"), 40)
            when = _card_field(item.get("date"), 32)
            snippet = _card_field(item.get("summary"), 140)
            if site:
                card["site"] = site
            if when:
                card["date"] = when
            if snippet:
                card["snippet"] = snippet
            items.append(card)
        return items, total
    if name != "web_fetch":
        return [], 0
    try:
        args = json.loads(arguments or "")
    except json.JSONDecodeError:
        return [], 0
    url = args.get("url") if isinstance(args, dict) else ""
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return [], 0
    title = payload.get("title")
    label = title.strip()[:80] if isinstance(title, str) and title.strip() else url[:80]
    return [{"title": label, "url": url[:2048]}], 1


class _WritePreview:
    """把 write_text_file 里已经生成的正文抽出来。参数还没写完也可以显示。"""

    def __init__(self) -> None:
        self._shown: dict[int, int] = {}

    def push(self, index: int, name: str, arguments: str) -> str:
        if name != "write_text_file":
            return ""
        text = _json_string_field(arguments, "content")
        if text is None:
            return ""
        shown = self._shown.get(index, 0)
        if len(text) <= shown:
            return ""
        self._shown[index] = len(text)
        return text[shown:]


def _json_string_field(raw: str, key: str) -> str | None:
    marker = f'"{key}"'
    pos = raw.find(marker)
    if pos < 0:
        return None
    index = pos + len(marker)
    while index < len(raw) and raw[index] in " \t\r\n":
        index += 1
    if index >= len(raw) or raw[index] != ":":
        return None
    index += 1
    while index < len(raw) and raw[index] in " \t\r\n":
        index += 1
    if index >= len(raw) or raw[index] != '"':
        return None
    index += 1
    chars: list[str] = []
    while index < len(raw):
        char = raw[index]
        if char == '"':
            return "".join(chars)
        if char != "\\":
            chars.append(char)
            index += 1
            continue
        if index + 1 >= len(raw):
            return "".join(chars)
        nxt = raw[index + 1]
        simple = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}
        if nxt in simple:
            chars.append(simple[nxt])
            index += 2
            continue
        if nxt == "u":
            hexpart = raw[index + 2 : index + 6]
            if len(hexpart) < 4 or any(item not in "0123456789abcdefABCDEF" for item in hexpart):
                return "".join(chars)
            chars.append(chr(int(hexpart, 16)))
            index += 6
            continue
        return "".join(chars)
    return "".join(chars)


def _absorb_tool_parts(
    slots: dict[int, dict[str, str]],
    parts: list[tuple[int, str | None, str | None, str | None]],
) -> None:
    for index, call_id, name, arguments in parts:
        slot = slots.setdefault(index, {"id": "", "name": "", "arguments": ""})
        if call_id:
            slot["id"] = call_id
        if name:
            slot["name"] += name
        if arguments:
            slot["arguments"] += arguments


def _calls_from_slots(slots: dict[int, dict[str, str]]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for index in sorted(slots):
        slot = slots[index]
        if not slot["id"] or not slot["name"]:
            raise InvalidModelResponseError()
        calls.append(ToolCall(id=slot["id"], name=slot["name"], arguments=slot["arguments"]))
    if not calls or len(calls) > 5:
        raise InvalidModelResponseError()
    return calls


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
