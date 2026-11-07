"""DeepSeek Chat Completions 的 AsyncOpenAI 适配器。"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from openai import AsyncOpenAI

from app.core.config import Settings
from app.core.context import get_request_id
from app.core.exceptions import (
    AppError,
    ConfigurationError,
    UpstreamError,
    is_retryable_provider_error,
    map_provider_exception,
)
from app.providers.base import CompletionResult, ProviderChatRequest
from app.providers.parsing import parse_completion, parse_stream_chunk

logger = logging.getLogger("qs_llm")
T = TypeVar("T")


async def close_stream(stream: Any) -> None:
    if stream is None:
        return
    for method_name in ("close", "aclose"):
        method = getattr(stream, method_name, None)
        if method is None:
            continue
        result = method()
        if inspect.isawaitable(result):
            await result
        return


class DeepSeekProvider:
    """应用生命周期内复用一个 AsyncOpenAI 客户端。SDK 自身重试关闭，由这里按错误类型重试。"""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client

    @property
    def client(self) -> Any:
        return self._client

    async def startup(self) -> None:
        if self._client is not None or not self._settings.has_api_key():
            return
        self._client = AsyncOpenAI(
            api_key=self._settings.deepseek_api_key,
            base_url=self._settings.deepseek_base_url,
            timeout=self._settings.deepseek_timeout_seconds,
            max_retries=0,
        )

    async def aclose(self) -> None:
        client = self._client
        self._client = None
        if client is None:
            return
        close = getattr(client, "close", None)
        if close is None:
            return
        result = close()
        if inspect.isawaitable(result):
            await result

    async def create_chat_completion(self, request: ProviderChatRequest) -> CompletionResult:
        self._ensure_configured()
        kwargs = self._sdk_kwargs(request, stream=False)
        response = await self._call(lambda: self._client.chat.completions.create(**kwargs))
        return parse_completion(response)

    async def stream_chat_completion(self, request: ProviderChatRequest):
        self._ensure_configured()
        kwargs = self._sdk_kwargs(request, stream=True)
        stream = await self._call(lambda: self._client.chat.completions.create(**kwargs))
        try:
            async for chunk in stream:
                parsed = parse_stream_chunk(chunk)
                if parsed is not None:
                    yield parsed
        except AppError:
            raise
        except Exception as exc:
            raise map_provider_exception(exc) from None
        finally:
            await close_stream(stream)

    def ensure_ready(self) -> None:
        """在开始流式响应之前检查配置，让配置错误仍走 JSON，而不是 SSE。"""
        self._ensure_configured()

    def _ensure_configured(self) -> None:
        if not self._settings.has_api_key() or self._client is None:
            raise ConfigurationError()

    def _sdk_kwargs(self, request: ProviderChatRequest, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": request.messages,
            "stream": stream,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.reasoning_effort is not None:
            payload["reasoning_effort"] = request.reasoning_effort
        if request.response_format is not None:
            payload["response_format"] = request.response_format
        if request.tools:
            payload["tools"] = request.tools
            payload["tool_choice"] = request.tool_choice or "auto"
        if request.thinking is not None:
            # thinking 不是 Chat Completions 的官方 SDK 字段，按 DeepSeek 文档放进 extra_body。
            payload["extra_body"] = {"thinking": request.thinking}
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    async def _call(self, operation: Callable[[], Awaitable[T]]) -> T:
        attempts = self._settings.deepseek_max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                return await operation()
            except AppError:
                raise
            except Exception as exc:
                retry = is_retryable_provider_error(exc) and attempt < attempts
                logger.warning(
                    "upstream call failed request_id=%s type=%s status=%s attempt=%s retry=%s",
                    get_request_id(),
                    type(exc).__name__,
                    getattr(exc, "status_code", None),
                    attempt,
                    retry,
                )
                if not retry:
                    raise map_provider_exception(exc) from None
                delay = self._settings.deepseek_retry_backoff_seconds * attempt
                if delay > 0:
                    await asyncio.sleep(delay)
        raise UpstreamError()
