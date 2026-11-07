"""SSE。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.context import reset_request_id, set_request_id
from app.core.exceptions import UpstreamTimeoutError
from app.providers.base import StreamChunk, UsageStats
from app.schemas.chat import StreamChatRequest
from app.services.chat_service import ChatService
from tests.fakes import FakeProvider, chat_payload


def test_sse_delta_usage_and_done(client: TestClient, provider: FakeProvider) -> None:
    provider.push(
        [
            StreamChunk(content="你", id="s1", model="deepseek-flash"),
            StreamChunk(
                content="好",
                id="s1",
                model="deepseek-flash",
                finish_reason="stop",
                usage=UsageStats(1, 2, 3),
                reasoning_content="隐藏思维链",
            ),
        ]
    )
    with client.stream("POST", "/api/v1/chat/completions/stream", json=chat_payload()) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = response.read().decode("utf-8")
    assert "event: delta" in body
    assert '"content":"你"' in body
    assert '"content":"好"' in body
    assert "event: usage" in body
    assert '"total_tokens":3' in body
    assert "event: done" in body
    assert '"finish_reason":"stop"' in body
    assert _event_names(body) == ["delta", "usage", "delta", "done"]
    assert "隐藏思维链" not in body
    assert provider.calls[0].messages[0]["role"] == "system"
    assert provider.stream_closed == 1


def test_sse_error_event_hides_upstream_detail(client: TestClient, provider: FakeProvider) -> None:
    provider.push(UpstreamTimeoutError())
    with client.stream("POST", "/api/v1/chat/completions/stream", json=chat_payload()) as response:
        body = response.read().decode("utf-8")
    assert "event: error" in body
    assert "UPSTREAM_TIMEOUT" in body
    assert "Traceback" not in body
    assert "event: done" not in body


def test_stream_false_is_rejected(client: TestClient, provider: FakeProvider) -> None:
    response = client.post(
        "/api/v1/chat/completions/stream",
        json=chat_payload(stream=False),
    )
    assert response.status_code == 422
    assert provider.calls == []


def test_disconnect_closes_upstream_stream(app, provider: FakeProvider) -> None:
    provider.push(
        [
            StreamChunk(content="你", id="s1", model="deepseek-flash"),
            StreamChunk(content="好", finish_reason="stop", id="s1", model="deepseek-flash"),
        ]
    )
    service = _service(app, provider)
    token = set_request_id("req-disconnect")
    try:
        prepared = service.prepare_chat(StreamChatRequest.model_validate(chat_payload()))

        async def connected() -> bool:
            return False

        async def consume() -> None:
            generator = service.iter_sse(prepared, connected)
            first = await generator.__anext__()
            assert "event: delta" in first
            await generator.aclose()

        import asyncio

        asyncio.run(consume())
    finally:
        reset_request_id(token)
    assert provider.stream_closed == 1


def test_disconnect_flag_stops_before_done(app, provider: FakeProvider) -> None:
    provider.push(
        [
            StreamChunk(content="你", id="s1", model="deepseek-flash"),
            StreamChunk(content="好", finish_reason="stop", id="s1", model="deepseek-flash"),
        ]
    )
    service = _service(app, provider)
    token = set_request_id("req-disconnect-flag")
    seen: list[str] = []

    async def disconnected() -> bool:
        return True

    async def consume() -> None:
        prepared = service.prepare_chat(StreamChatRequest.model_validate(chat_payload()))
        async for frame in service.iter_sse(prepared, disconnected):
            seen.append(frame)

    try:
        import asyncio

        asyncio.run(consume())
    finally:
        reset_request_id(token)
    assert seen == []
    assert provider.stream_closed == 1


def _event_names(body: str) -> list[str]:
    names: list[str] = []
    for block in body.split("\n\n"):
        first = block.strip().splitlines()[:1]
        if first and first[0].startswith("event: "):
            names.append(first[0].removeprefix("event: "))
    return names


def _service(app, provider: FakeProvider) -> ChatService:
    from app.services.prompt_service import PromptService
    from app.services.tool_service import ToolService
    from app.tools.registry import build_default_registry

    return ChatService(
        settings=app.state.settings,
        provider=provider,
        prompts=PromptService(),
        tools=ToolService(build_default_registry(), app.state.settings),
    )
