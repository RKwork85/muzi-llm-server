"""测试替身与请求样本。"""

from __future__ import annotations

from typing import Any

from app.core.exceptions import InvalidModelResponseError
from app.providers.base import (
    CompletionResult,
    ProviderChatRequest,
    StreamChunk,
    ToolCall,
    UsageStats,
)


def chat_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "messages": [{"role": "user", "content": "解释一下 FastAPI 的依赖注入"}],
        "prompt_name": "default",
    }
    payload.update(overrides)
    return payload


def make_result(
    content: str = "你好",
    *,
    tool_calls: list[ToolCall] | None = None,
    finish_reason: str | None = None,
    reasoning: str | None = None,
    completion_id: str = "chatcmpl-test",
    model: str = "deepseek-flash",
    usage: UsageStats | None = None,
) -> CompletionResult:
    calls = tool_calls or []
    if finish_reason is None:
        finish_reason = "tool_calls" if calls else "stop"
    return CompletionResult(
        id=completion_id,
        model=model,
        content=content,
        reasoning_content=reasoning,
        tool_calls=calls,
        finish_reason=finish_reason,
        usage=usage if usage is not None else UsageStats(10, 5, 15),
    )


def make_tool_call(
    call_id: str = "call_calc",
    name: str = "calculator",
    arguments: str = '{"expression":"23 * 19"}',
) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments)


class FakeProvider:
    def __init__(self) -> None:
        self._queue: list[Any] = []
        self.calls: list[ProviderChatRequest] = []
        self.closed = False
        self.stream_closed = 0

    def push(self, item: Any) -> None:
        self._queue.append(item)

    def _pop(self) -> Any:
        if not self._queue:
            raise InvalidModelResponseError()
        return self._queue.pop(0)

    async def startup(self) -> None:
        return None

    async def aclose(self) -> None:
        self.closed = True

    async def create_chat_completion(self, request: ProviderChatRequest) -> CompletionResult:
        self.calls.append(request)
        item = self._pop()
        if isinstance(item, Exception):
            raise item
        return item

    async def stream_chat_completion(self, request: ProviderChatRequest):
        self.calls.append(request)
        item = self._pop()
        try:
            if isinstance(item, Exception):
                raise item
            for chunk in item:
                if isinstance(chunk, Exception):
                    raise chunk
                yield chunk
        finally:
            self.stream_closed += 1


class ScriptedCompletions:
    def __init__(self, results: list[Any]) -> None:
        self.results = list(results)
        self.calls = 0
        self.kwargs: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls += 1
        self.kwargs.append(kwargs)
        if not self.results:
            raise RuntimeError("scripted client has no result")
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class ScriptedClient:
    def __init__(self, results: list[Any]) -> None:
        self.completions = ScriptedCompletions(results)
        self.chat = type("Chat", (), {"completions": self.completions})()
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def http_stack():
    try:
        import httpx2 as http
    except ImportError:
        import httpx as http
    return http


def completion_namespace(content: str = "你好") -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        id="chatcmpl-sdk",
        model="deepseek-flash",
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content=content, reasoning_content=None, tool_calls=None),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=2, completion_tokens=3, total_tokens=5),
    )
