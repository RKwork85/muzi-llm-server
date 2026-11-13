"""把现有 Provider 收成 Agent 可用的结果，并把整次调用包进剩余 Deadline。"""

from __future__ import annotations

import asyncio

from app.agents.models import UsageSnapshot
from app.agents.policy import RunClock
from app.core.exceptions import (
    AppError,
    ConfigurationError,
    InvalidModelResponseError,
    UpstreamAuthError,
    UpstreamError,
    UpstreamRateLimitError,
    UpstreamTimeoutError,
)
from app.providers.base import CompletionResult, LLMProvider, ProviderChatRequest, ToolCall

_KNOWN_FINISH = frozenset(
    {
        "stop",
        "tool_calls",
        "length",
        "content_filter",
        "insufficient_system_resource",
        "aborted",
    }
)


class ProviderFailure(Exception):
    """给循环看的安全错误。只有类别和错误码，没有上游正文。"""

    def __init__(self, category: str, code: str) -> None:
        self.category = category
        self.code = code
        super().__init__(code)


class NormalizedToolCall:
    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        self.id = call_id
        self.name = name
        self.arguments = arguments


class NormalizedCompletion:
    def __init__(
        self,
        *,
        content: str | None,
        reasoning_content: str | None,
        tool_calls: list[NormalizedToolCall],
        finish_reason: str,
        usage: UsageSnapshot | None,
    ) -> None:
        self.content = content
        self.reasoning_content = reasoning_content
        self.tool_calls = tool_calls
        self.finish_reason = finish_reason
        self.usage = usage


def classify_finish_reason(value: str | None) -> str:
    if value in _KNOWN_FINISH:
        return str(value)
    return "unknown"


async def complete_for_agent(
    provider: LLMProvider,
    request: ProviderChatRequest,
    clock: RunClock,
    *,
    now: float | None = None,
) -> NormalizedCompletion:
    """timeout 取模型预算和剩余墙钟的较小值，包住 Provider 内部重试。"""
    remaining = clock.remaining(now)
    budget = clock.model_timeout(now)
    if budget <= 0 or remaining <= 0:
        raise ProviderFailure("timeout", "AGENT_DEADLINE_EXCEEDED")
    bounded_by_wall = remaining <= clock.policy.model_timeout_seconds
    try:
        async with asyncio.timeout(budget):
            result = await provider.create_chat_completion(request)
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        if bounded_by_wall:
            raise ProviderFailure("timeout", "AGENT_DEADLINE_EXCEEDED") from None
        raise ProviderFailure("timeout", "UPSTREAM_TIMEOUT") from None
    except ProviderFailure:
        raise
    except Exception as exc:
        raise _failure_from_exception(exc) from None
    return normalize_completion(result)


def normalize_completion(result: CompletionResult) -> NormalizedCompletion:
    calls = [_normalize_call(call) for call in result.tool_calls]
    usage = None
    if result.usage is not None:
        usage = UsageSnapshot(
            prompt_tokens=max(0, int(result.usage.prompt_tokens)),
            completion_tokens=max(0, int(result.usage.completion_tokens)),
            total_tokens=max(0, int(result.usage.total_tokens)),
            reasoning_tokens=_opt_int(result.usage.reasoning_tokens),
            cache_read_tokens=_opt_int(result.usage.cache_read_tokens),
            cache_write_tokens=_opt_int(result.usage.cache_write_tokens),
        )
    content = result.content if isinstance(result.content, str) else None
    reasoning = result.reasoning_content if isinstance(result.reasoning_content, str) else None
    return NormalizedCompletion(
        content=content,
        reasoning_content=reasoning,
        tool_calls=calls,
        finish_reason=classify_finish_reason(result.finish_reason),
        usage=usage,
    )


def _normalize_call(call: ToolCall) -> NormalizedToolCall:
    arguments = call.arguments if isinstance(call.arguments, str) else ""
    return NormalizedToolCall(str(call.id or ""), str(call.name or ""), arguments)


def _opt_int(value: int | None) -> int | None:
    if value is None:
        return None
    return max(0, int(value))


def _failure_from_exception(exc: Exception) -> ProviderFailure:
    status = getattr(exc, "status_code", None)
    upstream_status = getattr(exc, "upstream_status", None)
    name = type(exc).__name__
    if status == 402 or upstream_status == 402 or name in {"InsufficientQuotaError", "PaymentRequiredError"}:
        return ProviderFailure("balance", "UPSTREAM_BALANCE")
    if isinstance(exc, UpstreamAuthError) or status in {401, 403}:
        return ProviderFailure("auth", "UPSTREAM_AUTH_ERROR")
    if isinstance(exc, InvalidModelResponseError) or status in {400, 422}:
        return ProviderFailure("invalid_request", "INVALID_MODEL_RESPONSE")
    if isinstance(exc, UpstreamRateLimitError) or status == 429 or name == "RateLimitError":
        return ProviderFailure("rate_limit", "UPSTREAM_RATE_LIMIT")
    if isinstance(exc, UpstreamTimeoutError) or isinstance(exc, TimeoutError) or name == "APITimeoutError":
        return ProviderFailure("timeout", "UPSTREAM_TIMEOUT")
    if name == "APIConnectionError":
        return ProviderFailure("network", "UPSTREAM_ERROR")
    if isinstance(exc, ConfigurationError):
        return ProviderFailure("invalid_request", "CONFIGURATION_ERROR")
    if isinstance(exc, UpstreamError) or (isinstance(status, int) and status >= 500):
        return ProviderFailure("unavailable", "UPSTREAM_ERROR")
    if isinstance(exc, AppError):
        return ProviderFailure("unavailable", exc.code)
    return ProviderFailure("unavailable", "UPSTREAM_ERROR")


def redact_exception(exc: BaseException) -> str:
    """日志只用类型名。"""
    return type(exc).__name__
