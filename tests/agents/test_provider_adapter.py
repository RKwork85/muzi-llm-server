"""Provider 适配：Deadline 包住重试，错误映射不带密钥。"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.agents.policy import RunClock, server_policy_from_settings
from app.agents.provider_adapter import complete_for_agent, normalize_completion
from app.core.exceptions import UpstreamAuthError
from app.providers.base import ProviderChatRequest
from app.providers.deepseek import DeepSeekProvider
from app.tools.registry import build_default_registry
from tests.conftest import make_settings
from tests.fakes import FakeProvider, ScriptedClient, completion_namespace, make_result, make_tool_call


def _clock_with_remaining(seconds: float) -> RunClock:
    policy = server_policy_from_settings(make_settings(), build_default_registry())
    started = time.monotonic() - (policy.max_wall_time_seconds - seconds)
    return RunClock.start(policy, now=started)


def _request() -> ProviderChatRequest:
    return ProviderChatRequest(model="deepseek-flash", messages=[{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_adapter_maps_tool_calls_and_known_finish() -> None:
    provider = FakeProvider()
    provider.push(
        make_result(
            content=None,
            tool_calls=[make_tool_call()],
            finish_reason="tool_calls",
            reasoning="hidden",
        )
    )
    result = await complete_for_agent(provider, _request(), _clock_with_remaining(5))
    assert result.finish_reason == "tool_calls"
    assert result.tool_calls[0].id == "call_calc"
    assert result.tool_calls[0].name == "calculator"
    assert result.tool_calls[0].arguments == '{"expression":"23 * 19"}'
    assert result.reasoning_content == "hidden"
    assert result.usage is not None
    assert result.usage.total_tokens == 15


@pytest.mark.asyncio
async def test_unknown_finish_is_not_stop() -> None:
    normalized = normalize_completion(make_result(finish_reason="mystery"))
    assert normalized.finish_reason == "unknown"


@pytest.mark.asyncio
async def test_timeout_stays_inside_remaining_budget() -> None:
    class SlowProvider:
        async def create_chat_completion(self, request: ProviderChatRequest):
            await asyncio.sleep(2)
            return make_result("late")

    started = time.perf_counter()
    with pytest.raises(Exception) as caught:
        await complete_for_agent(SlowProvider(), _request(), _clock_with_remaining(0.1))
    elapsed = time.perf_counter() - started
    assert caught.value.code == "AGENT_DEADLINE_EXCEEDED"
    assert "late" not in str(caught.value)
    assert elapsed < 0.6


@pytest.mark.asyncio
async def test_provider_retries_cannot_outrun_deadline() -> None:
    class APITimeoutError(Exception):
        pass

    settings = make_settings(deepseek_max_retries=2, deepseek_retry_backoff_seconds=0.4)
    client = ScriptedClient([APITimeoutError(), APITimeoutError(), completion_namespace("不应返回")])
    provider = DeepSeekProvider(settings, client=client)
    started = time.perf_counter()
    with pytest.raises(Exception) as caught:
        await complete_for_agent(provider, _request(), _clock_with_remaining(0.15))
    elapsed = time.perf_counter() - started
    assert caught.value.code == "AGENT_DEADLINE_EXCEEDED"
    assert elapsed < 0.6
    assert client.completions.calls <= 2


@pytest.mark.asyncio
async def test_auth_error_does_not_expose_secret() -> None:
    provider = FakeProvider()
    provider.push(UpstreamAuthError("sk-super-secret"))
    with pytest.raises(Exception) as caught:
        await complete_for_agent(provider, _request(), _clock_with_remaining(5))
    assert caught.value.code == "UPSTREAM_AUTH_ERROR"
    assert caught.value.category == "auth"
    assert "sk-super-secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_balance_status_is_distinct_when_provider_raises_it() -> None:
    class PaymentRequiredError(Exception):
        status_code = 402

    provider = FakeProvider()
    provider.push(PaymentRequiredError())
    with pytest.raises(Exception) as caught:
        await complete_for_agent(provider, _request(), _clock_with_remaining(5))
    assert caught.value.category == "balance"
    assert caught.value.code == "UPSTREAM_BALANCE"


@pytest.mark.asyncio
async def test_provider_mapped_402_stays_balance_for_agent() -> None:
    class PaymentRequiredError(Exception):
        status_code = 402

    settings = make_settings(deepseek_max_retries=2, deepseek_retry_backoff_seconds=0)
    client = ScriptedClient([PaymentRequiredError("sk-balance-secret")])
    provider = DeepSeekProvider(settings, client=client)
    with pytest.raises(Exception) as caught:
        await complete_for_agent(provider, _request(), _clock_with_remaining(5))
    assert caught.value.category == "balance"
    assert caught.value.code == "UPSTREAM_BALANCE"
    assert "sk-balance-secret" not in str(caught.value)
    assert client.completions.calls == 1
