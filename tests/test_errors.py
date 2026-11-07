"""上游错误映射、Provider 重试和生命周期。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import UpstreamAuthError, UpstreamError, UpstreamTimeoutError, map_provider_exception
from app.providers.base import ProviderChatRequest
from app.providers.deepseek import DeepSeekProvider
from app.providers.parsing import parse_completion, parse_stream_chunk
from openai import APITimeoutError, AuthenticationError, InternalServerError, PermissionDeniedError, RateLimitError
from tests.conftest import make_settings
from tests.fakes import ScriptedClient, chat_payload, completion_namespace, http_stack


def _request():
    http = http_stack()
    return http.Request("POST", "https://api.deepseek.com/chat/completions")


def _status_error(status: int, error_type, message: str):
    http = http_stack()
    response = http.Response(status, request=_request())
    return error_type(message, response=response, body={"error": message})


def test_parse_completion_and_stream_chunk() -> None:
    parsed = parse_completion(completion_namespace("你好"))
    assert parsed.content == "你好"
    assert parsed.usage is not None
    assert parsed.usage.total_tokens == 5
    from types import SimpleNamespace

    chunk = SimpleNamespace(
        id="chunk-1",
        model="deepseek-flash",
        choices=[SimpleNamespace(finish_reason=None, delta=SimpleNamespace(content="你", reasoning_content=None))],
        usage=None,
    )
    streamed = parse_stream_chunk(chunk)
    assert streamed is not None
    assert streamed.content == "你"


@pytest.mark.asyncio
async def test_startup_uses_configured_base_url_and_disables_sdk_retry() -> None:
    settings = make_settings()
    provider = DeepSeekProvider(settings)
    await provider.startup()
    try:
        assert provider.client.max_retries == 0
        assert "api.deepseek.com" in str(provider.client.base_url)
        assert "chat/completions" not in str(provider.client.base_url)
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_retry_then_success_and_auth_is_not_retried() -> None:
    settings = make_settings(deepseek_max_retries=2, deepseek_retry_backoff_seconds=0)
    timeout = APITimeoutError(_request())
    scripted = ScriptedClient([timeout, timeout, completion_namespace("重试后成功")])
    provider = DeepSeekProvider(settings, client=scripted)
    result = await provider.create_chat_completion(
        ProviderChatRequest(model="deepseek-flash", messages=[{"role": "user", "content": "hi"}])
    )
    assert result.content == "重试后成功"
    assert scripted.completions.calls == 3

    secret = "sk-leak-should-not-surface"
    denied = ScriptedClient([_status_error(401, AuthenticationError, secret)])
    provider = DeepSeekProvider(settings, client=denied)
    with pytest.raises(UpstreamAuthError):
        await provider.create_chat_completion(
            ProviderChatRequest(model="deepseek-flash", messages=[{"role": "user", "content": "hi"}])
        )
    assert denied.completions.calls == 1


@pytest.mark.asyncio
async def test_provider_stream_parses_chunks() -> None:
    async def chunks():
        from types import SimpleNamespace

        yield SimpleNamespace(
            id="c1",
            model="deepseek-flash",
            choices=[SimpleNamespace(finish_reason="stop", delta=SimpleNamespace(content="好", reasoning_content=None))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

    settings = make_settings(deepseek_max_retries=0)
    scripted = ScriptedClient([chunks()])
    provider = DeepSeekProvider(settings, client=scripted)
    seen = []
    async for item in provider.stream_chat_completion(
        ProviderChatRequest(
            model="deepseek-flash",
            messages=[{"role": "user", "content": "hi"}],
            thinking={"type": "disabled"},
        )
    ):
        seen.append(item)
    assert seen[0].content == "好"
    assert seen[0].usage is not None
    assert scripted.completions.kwargs[0]["stream"] is True
    assert scripted.completions.kwargs[0]["stream_options"] == {"include_usage": True}
    assert scripted.completions.kwargs[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert settings.deepseek_api_key not in json.dumps(scripted.completions.kwargs)


def test_http_error_mapping(settings) -> None:
    cases = [
        (401, AuthenticationError, "UPSTREAM_AUTH_ERROR", 401),
        (403, PermissionDeniedError, "UPSTREAM_AUTH_ERROR", 401),
        (429, RateLimitError, "UPSTREAM_RATE_LIMIT", 429),
        (500, InternalServerError, "UPSTREAM_ERROR", 502),
    ]
    from app.main import create_app

    for status, error_type, code, expected in cases:
        secret = f"sk-upstream-secret-{status}"
        runtime = make_settings(deepseek_max_retries=0)
        scripted = ScriptedClient([_status_error(status, error_type, secret)])
        app = create_app(
            settings=runtime,
            provider=DeepSeekProvider(runtime, client=scripted),
        )
        with TestClient(app) as client:
            response = client.post("/api/v1/chat/completions", json=chat_payload())
        assert response.status_code == expected
        assert response.json()["error"]["code"] == code
        assert secret not in response.text
        assert "Traceback" not in response.text
        assert scripted.completions.calls == 1


def test_http_402_keeps_phase1_code_and_upstream_status() -> None:
    class PaymentRequiredError(Exception):
        status_code = 402

    mapped = map_provider_exception(PaymentRequiredError("balance"))
    assert mapped.code == "UPSTREAM_ERROR"
    assert mapped.status_code == 502
    assert mapped.upstream_status == 402

    from app.main import create_app

    scripted = ScriptedClient([PaymentRequiredError("sk-balance-secret")])
    runtime = make_settings(deepseek_max_retries=2, deepseek_retry_backoff_seconds=0)
    app = create_app(settings=runtime, provider=DeepSeekProvider(runtime, client=scripted))
    with TestClient(app) as client:
        response = client.post("/api/v1/chat/completions", json=chat_payload())
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_ERROR"
    assert "sk-balance-secret" not in response.text
    assert scripted.completions.calls == 1


def test_timeout_retries_then_maps(settings) -> None:
    from app.main import create_app

    scripted = ScriptedClient([APITimeoutError(_request()) for _ in range(3)])
    app = create_app(
        settings=make_settings(deepseek_max_retries=2, deepseek_retry_backoff_seconds=0),
        provider=DeepSeekProvider(settings, client=scripted),
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/chat/completions", json=chat_payload())
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "UPSTREAM_TIMEOUT"
    assert "Request timed out" not in response.text
    assert scripted.completions.calls == 3


def test_lifespan_closes_injected_client(settings) -> None:
    from app.main import create_app

    scripted = ScriptedClient([])
    app = create_app(settings=settings, provider=DeepSeekProvider(settings, client=scripted))
    with TestClient(app):
        assert scripted.closed is False
    assert scripted.closed is True


def test_missing_key_http_error() -> None:
    from app.main import create_app

    settings = make_settings(deepseek_api_key="")
    app = create_app(settings=settings, provider=DeepSeekProvider(settings))
    with TestClient(app) as client:
        response = client.post("/api/v1/chat/completions", json=chat_payload())
        ready = client.get("/ready")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "CONFIGURATION_ERROR"
    assert ready.json() == {"status": "not_ready", "reason": "missing_api_key"}
    assert "sk-" not in response.text
    assert "sk-" not in ready.text


def test_stream_missing_key_stays_json() -> None:
    from app.main import create_app

    settings = make_settings(deepseek_api_key="")
    app = create_app(settings=settings, provider=DeepSeekProvider(settings))
    with TestClient(app) as client:
        response = client.post("/api/v1/chat/completions/stream", json=chat_payload())
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "CONFIGURATION_ERROR"
    assert "sk-" not in response.text


def test_sse_timeout_is_error_event() -> None:
    from app.main import create_app

    settings = make_settings(deepseek_max_retries=0)
    scripted = ScriptedClient([APITimeoutError(_request())])
    app = create_app(settings=settings, provider=DeepSeekProvider(settings, client=scripted))
    with TestClient(app) as client:
        with client.stream("POST", "/api/v1/chat/completions/stream", json=chat_payload()) as response:
            body = response.read().decode("utf-8")
    assert "event: error" in body
    assert "UPSTREAM_TIMEOUT" in body
    assert "Request timed out" not in body
    assert "Traceback" not in body
