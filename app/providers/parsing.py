"""把 DeepSeek / OpenAI SDK 响应收成内部结构。"""

from __future__ import annotations

import json
from typing import Any

from app.core.exceptions import InvalidModelResponseError
from app.providers.base import CompletionResult, StreamChunk, ToolCall, UsageStats

_MAX_TOOL_CALLS = 5


def read_field(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    value = getattr(obj, name, None)
    if value is not None:
        return value
    for extra_name in ("model_extra", "__pydantic_extra__"):
        extra = getattr(obj, extra_name, None)
        if isinstance(extra, dict) and name in extra and extra[name] is not None:
            return extra[name]
    if hasattr(obj, name):
        return getattr(obj, name)
    return default


def parse_usage(raw: Any) -> UsageStats | None:
    if raw is None:
        return None
    return UsageStats(
        prompt_tokens=int(read_field(raw, "prompt_tokens", 0) or 0),
        completion_tokens=int(read_field(raw, "completion_tokens", 0) or 0),
        total_tokens=int(read_field(raw, "total_tokens", 0) or 0),
    )


def parse_tool_calls(raw_calls: Any) -> list[ToolCall]:
    calls = list(raw_calls or [])
    if len(calls) > _MAX_TOOL_CALLS:
        raise InvalidModelResponseError()
    parsed: list[ToolCall] = []
    for raw in calls:
        call_id = read_field(raw, "id")
        function = read_field(raw, "function")
        name = read_field(function, "name") if function is not None else None
        arguments = read_field(function, "arguments") if function is not None else None
        if not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not name:
            raise InvalidModelResponseError()
        if isinstance(arguments, dict):
            arguments = json.dumps(arguments, ensure_ascii=False)
        elif arguments is None:
            arguments = ""
        elif not isinstance(arguments, str):
            raise InvalidModelResponseError()
        parsed.append(ToolCall(id=call_id, name=name, arguments=arguments))
    return parsed


def parse_completion(response: Any) -> CompletionResult:
    choices = read_field(response, "choices")
    if not choices:
        raise InvalidModelResponseError()
    choice = choices[0]
    message = read_field(choice, "message")
    if message is None:
        raise InvalidModelResponseError()
    tool_calls = parse_tool_calls(read_field(message, "tool_calls"))
    content = read_field(message, "content")
    if content is not None and not isinstance(content, str):
        raise InvalidModelResponseError()
    reasoning = read_field(message, "reasoning_content")
    if reasoning is not None and not isinstance(reasoning, str):
        reasoning = None
    if content is None and not tool_calls and not (reasoning and reasoning.strip()):
        raise InvalidModelResponseError()
    finish = read_field(choice, "finish_reason")
    if not isinstance(finish, str) or not finish:
        finish = "tool_calls" if tool_calls else "stop"
    return CompletionResult(
        id=str(read_field(response, "id") or ""),
        model=str(read_field(response, "model") or ""),
        content=content,
        reasoning_content=reasoning,
        tool_calls=tool_calls,
        finish_reason=finish,
        usage=parse_usage(read_field(response, "usage")),
    )


def parse_stream_chunk(chunk: Any) -> StreamChunk | None:
    if chunk is None:
        raise InvalidModelResponseError()
    usage = parse_usage(read_field(chunk, "usage"))
    choices = read_field(chunk, "choices") or []
    content = None
    reasoning = None
    finish = None
    if choices:
        choice = choices[0]
        finish_reason = read_field(choice, "finish_reason")
        finish = finish_reason if isinstance(finish_reason, str) else None
        delta = read_field(choice, "delta")
        if delta is not None:
            raw_content = read_field(delta, "content")
            raw_reasoning = read_field(delta, "reasoning_content")
            content = raw_content if isinstance(raw_content, str) else None
            reasoning = raw_reasoning if isinstance(raw_reasoning, str) else None
    if content is None and reasoning is None and finish is None and usage is None:
        return None
    model = read_field(chunk, "model")
    chunk_id = read_field(chunk, "id")
    return StreamChunk(
        content=content,
        reasoning_content=reasoning,
        finish_reason=finish,
        usage=usage,
        model=str(model) if model else None,
        id=str(chunk_id) if chunk_id else None,
    )
