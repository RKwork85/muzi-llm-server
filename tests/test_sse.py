"""SSE。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.core.context import reset_request_id, set_request_id
from app.core.exceptions import UpstreamTimeoutError
from app.providers.base import StreamChunk, UsageStats
from app.schemas.chat import StreamChatRequest
from app.services.chat_service import (
    ChatService,
    _WritePreview,
    _json_string_field,
    _tool_cards,
    _tool_public,
)
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


def test_plan_stream_injects_plan_mode_prompt(app, provider: FakeProvider) -> None:
    from app.services.prompt_service import PromptService

    service = _service(app, provider)
    request = StreamChatRequest.model_validate(chat_payload(mode="plan"))
    prepared = service.prepare_stream(request)

    system = prepared.provider_request.messages[0]["content"]
    assert "你当前处于 Plan 模式" in system
    assert PromptService().render("plan_mode")[:40] in system
    # 文件存在时不应再拼上内置的精简说明
    assert "你处于 Plan 模式。只能使用只读工具了解情况" not in system


def test_ask_stream_does_not_inject_plan_mode_prompt(app, provider: FakeProvider) -> None:
    service = _service(app, provider)
    prepared = service.prepare_stream(StreamChatRequest.model_validate(chat_payload()))

    system = prepared.provider_request.messages[0]["content"]
    assert "你当前处于 Plan 模式" not in system


def test_ask_stream_runs_tool_then_answers(app, provider: FakeProvider) -> None:
    import asyncio

    from app.services.tool_service import ToolService
    from app.tools.registry import build_agent_registry

    service = _service(app, provider)
    service.ask_tools = ToolService(build_agent_registry(), app.state.settings)
    provider.push(
        [
            StreamChunk(
                id="s1",
                model="deepseek-flash",
                finish_reason="tool_calls",
                tool_call_parts=[(0, "call_calc", "calculator", '{"expression":"23 * 19"}')],
                usage=UsageStats(10, 4, 14),
            )
        ]
    )
    provider.push(
        [
            StreamChunk(
                content="437",
                id="s2",
                model="deepseek-flash",
                finish_reason="stop",
                usage=UsageStats(20, 2, 22),
            )
        ]
    )
    token = set_request_id("req-ask")
    try:
        prepared = service.prepare_stream(StreamChatRequest.model_validate(chat_payload()))

        async def connected() -> bool:
            return False

        async def collect() -> str:
            frames: list[str] = []
            async for frame in service.iter_sse(prepared, connected):
                frames.append(frame)
            return "".join(frames)

        body = asyncio.run(collect())
    finally:
        reset_request_id(token)
    assert provider.calls[0].tool_choice == "auto"
    assert "event: tool" in body
    assert '"name":"calculator"' in body
    assert '"content":"437"' in body
    assert body.index("event: tool") < body.index("event: delta")
    assert "event: done" in body
    assert '"total_tokens":14' in body
    assert '"total_tokens":22' in body
    assert any(item.get("role") == "tool" for item in provider.calls[1].messages)


def test_tool_public_keeps_query_and_result() -> None:
    detail, preview = _tool_public(
        "weather_query",
        '{"place":"广州"}',
        '{"source":"server_tool","name":"weather_query","result":"{\\"place\\":\\"广州\\",\\"condition\\":\\"多云\\",\\"temperature_c\\":26}"}',
    )
    assert detail == "广州"
    assert "多云" in preview
    assert "26" in preview


def test_tool_round_text_becomes_a_note(app, provider: FakeProvider) -> None:
    import asyncio

    from app.services.tool_service import ToolService
    from app.tools.registry import build_agent_registry

    service = _service(app, provider)
    service.ask_tools = ToolService(build_agent_registry(), app.state.settings)
    provider.push(
        [
            StreamChunk(content="先查一下。", id="s1", model="deepseek-flash"),
            StreamChunk(
                id="s1",
                model="deepseek-flash",
                finish_reason="tool_calls",
                tool_call_parts=[(0, "call_calc", "calculator", '{"expression":"1+1"}')],
            ),
        ]
    )
    provider.push([StreamChunk(content="2", id="s2", model="deepseek-flash", finish_reason="stop")])
    token = set_request_id("req-note")
    try:
        prepared = service.prepare_stream(StreamChatRequest.model_validate(chat_payload()))

        async def connected() -> bool:
            return False

        async def collect() -> str:
            frames: list[str] = []
            async for frame in service.iter_sse(prepared, connected):
                frames.append(frame)
            return "".join(frames)

        body = asyncio.run(collect())
    finally:
        reset_request_id(token)
    assert "event: note" in body
    assert '"content":"先查一下。"' in body
    assert '"rewind":5' in body
    note_at = body.index("event: note")
    assert body.index("event: delta") < note_at
    assert body.index("event: tool") > note_at
    assert '"content":"2"' in body


def test_tool_cards_keep_titles_without_page_text() -> None:
    import json

    content = json.dumps(
        {
            "source": "server_tool",
            "name": "web_search",
            "result": json.dumps(
                {
                    "results": [
                        {
                            "title": "头条",
                            "url": "https://news.example/a",
                            "summary": ("甲" * 140) + ("乙" * 40),
                            "site_name": "示例网",
                            "date": "2026-11-07",
                        },
                        {"title": "第二条", "url": "https://news.example/b", "summary": "x"},
                    ]
                },
                ensure_ascii=False,
            ),
        },
        ensure_ascii=False,
    )
    items, count = _tool_cards("web_search", "{}", content)
    assert count == 2
    assert items[0] == {
        "title": "头条",
        "url": "https://news.example/a",
        "site": "示例网",
        "date": "2026-11-07",
        "snippet": "甲" * 140,
    }
    assert "乙" not in json.dumps(items, ensure_ascii=False)
    fetched, total = _tool_cards(
        "web_fetch",
        '{"url":"https://wiki.example/a"}',
        json.dumps({"source": "server_tool", "result": json.dumps({"title": "伊娜", "untrusted_content": "正文"})}),
    )
    assert total == 1
    assert fetched == [{"title": "伊娜", "url": "https://wiki.example/a"}]


def _wrapped_result(name: str, result: str) -> str:
    return json.dumps({"source": "server_tool", "name": name, "result": result}, ensure_ascii=False)


def test_truncated_search_counts_links_written_before_the_cut() -> None:
    payload = {
        "query": "金价",
        "results": [
            {"title": "第一条", "url": "https://news.example/a", "summary": "甲" * 400},
            {"title": "第二条", "url": "https://news.example/b", "summary": "乙" * 400},
            {"title": "第三条", "url": "https://news.example/c", "summary": "丙" * 400},
        ],
        "result_count": 3,
    }
    raw = json.dumps(payload, ensure_ascii=False)
    second = raw.index("https://news.example/b")
    cut_after_second = raw[: second + 80] + "...[truncated]"
    items, count = _tool_cards("web_search", "{}", _wrapped_result("web_search", cut_after_second))
    assert count == 2
    assert [item["url"] for item in items] == ["https://news.example/a", "https://news.example/b"]

    cut_inside_second = raw[: second + 8] + "...[truncated]"
    items, count = _tool_cards("web_search", "{}", _wrapped_result("web_search", cut_inside_second))
    assert count == 1
    assert items == [{"title": "第一条", "url": "https://news.example/a", "snippet": "甲" * 140}]


def test_truncated_fetch_still_counts_the_page() -> None:
    body = json.dumps(
        {
            "url": "https://wiki.example/a",
            "title": "伊娜",
            "untrusted_content": "正" * 500,
        },
        ensure_ascii=False,
    )
    content = _wrapped_result("web_fetch", body[:120] + "...[truncated]")
    items, count = _tool_cards("web_fetch", '{"url":"https://wiki.example/a"}', content)
    assert count == 1
    assert items == [{"title": "伊娜", "url": "https://wiki.example/a"}]


def test_diagram_source_keeps_the_chart_for_the_page() -> None:
    from app.services.chat_service import _diagram_source

    source = 'flowchart TB\n  n1["开始"]\n'
    wrapped = json.dumps(
        {"source": "server_tool", "result": json.dumps({"mermaid": source, "title": "流程"}, ensure_ascii=False)},
        ensure_ascii=False,
    )
    assert _diagram_source(wrapped) == source.strip()
    empty = json.dumps({"result": json.dumps({"title": "只有标题"})})
    assert _diagram_source(empty) == ""


def test_tool_error_code_does_not_count_as_pages() -> None:
    content = _wrapped_result("web_search", "web_bad_response")
    assert _tool_cards("web_search", "{}", content) == ([], 0)


def test_partial_write_text_is_visible_before_json_closes() -> None:
    assert _json_string_field('{"path":"a.md","content":"狗为什', "content") == "狗为什"
    assert _json_string_field('{"content":"第一行\\n第二行"}', "content") == "第一行\n第二行"
    preview = _WritePreview()
    assert preview.push(0, "write_text_file", '{"content":"你') == "你"
    assert preview.push(0, "write_text_file", '{"content":"你好"}') == "好"
    assert preview.push(0, "calculator", '{"expression":"1+1"}') == ""


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
