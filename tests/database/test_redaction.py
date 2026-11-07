"""工具参数和事件载荷里的密钥不能原样进入持久化对象。"""

from __future__ import annotations

from app.core.database.redaction import redact_sensitive, redact_text
from app.models.tables import AgentTask, ExecutionEvent, TaskExecution, ToolCallRecord


def test_redact_sensitive_keys_and_bearer_text() -> None:
    payload = {
        "city": "shanghai",
        "api_key": "super-secret-value",
        "nested": [{"password": "hunter22xx"}, "Authorization: Bearer abcdefghijklmnop"],
    }
    cleaned = redact_sensitive(payload)
    assert cleaned["city"] == "shanghai"
    assert cleaned["api_key"] == "***"
    assert cleaned["nested"][0]["password"] == "***"
    assert "abcdefghijklmnop" not in cleaned["nested"][1]
    assert "***" in cleaned["nested"][1]


def test_redact_text_keeps_ordinary_sentences() -> None:
    text = redact_text("今天上海多云，password=hunter22xx，其余正常")
    assert text is not None
    assert "hunter22xx" not in text
    assert "今天上海多云" in text


def test_listeners_are_registered() -> None:
    from sqlalchemy import event

    from app.core.database.redaction import (
        _redact_event,
        _redact_execution,
        _redact_task,
        _redact_tool_call,
    )

    assert event.contains(ToolCallRecord, "before_insert", _redact_tool_call)
    assert event.contains(ExecutionEvent, "before_insert", _redact_event)
    assert event.contains(AgentTask, "before_insert", _redact_task)
    assert event.contains(TaskExecution, "before_insert", _redact_execution)
