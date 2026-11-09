from __future__ import annotations

from app.api.v1.endpoints.chat import _stream_event


def test_stream_event_parser_preserves_chat_sse_event_and_payload() -> None:
    assert _stream_event('event: delta\ndata: {"content":"hello"}\n\n') == (
        "delta",
        {"content": "hello"},
    )
    assert _stream_event(": heartbeat\n\n") is None
    assert _stream_event('event: delta\ndata: not-json\n\n') is None
