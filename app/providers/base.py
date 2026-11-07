"""Provider 与服务之间的窄接口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class UsageStats:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class CompletionResult:
    id: str
    model: str
    content: str | None
    reasoning_content: str | None
    tool_calls: list[ToolCall]
    finish_reason: str
    usage: UsageStats | None = None


@dataclass
class StreamChunk:
    content: str | None = None
    reasoning_content: str | None = None
    finish_reason: str | None = None
    usage: UsageStats | None = None
    model: str | None = None
    id: str | None = None


@dataclass
class ProviderChatRequest:
    model: str
    messages: list[dict[str, Any]]
    temperature: float | None = None
    max_tokens: int | None = None
    thinking: dict[str, str] | None = None
    reasoning_effort: str | None = None
    response_format: dict[str, str] | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | None = None


class LLMProvider(Protocol):
    async def create_chat_completion(self, request: ProviderChatRequest) -> CompletionResult:
        """非流式补全。"""

    def stream_chat_completion(self, request: ProviderChatRequest):
        """流式补全，返回异步迭代器。"""

    async def aclose(self) -> None:
        """释放客户端。"""

    async def startup(self) -> None:
        """准备客户端。"""
