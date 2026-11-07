"""写入审计表前去掉密码、Token 和密钥。对话正文不在这里改写。"""

from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(password|passwd|secret|token|api[-_]?key|authorization|credential|private[-_]?key|access[-_]?key)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]{8,}")
_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|secret|token|api_key|apikey|authorization|credential)\b(\s*[:=]\s*)(\S+)"
)

_registered = False


def redact_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _BEARER.sub(r"\1***", value)
    return _ASSIGNMENT.sub(r"\1\2***", cleaned)


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _SENSITIVE_KEY.search(key):
                cleaned[key] = None if item is None else "***"
            else:
                cleaned[key] = redact_sensitive(item)
        return cleaned
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def register_redaction_listeners() -> None:
    global _registered
    if _registered:
        return
    from sqlalchemy import event

    from app.models.tables import AgentTask, ExecutionEvent, TaskExecution, ToolCallRecord

    event.listen(ToolCallRecord, "before_insert", _redact_tool_call)
    event.listen(ToolCallRecord, "before_update", _redact_tool_call)
    event.listen(ExecutionEvent, "before_insert", _redact_event)
    event.listen(ExecutionEvent, "before_update", _redact_event)
    event.listen(AgentTask, "before_insert", _redact_task)
    event.listen(AgentTask, "before_update", _redact_task)
    event.listen(TaskExecution, "before_insert", _redact_execution)
    event.listen(TaskExecution, "before_update", _redact_execution)
    _registered = True


def _redact_tool_call(_mapper: object, _connection: object, target: Any) -> None:
    target.arguments = redact_sensitive(target.arguments)
    target.result = redact_sensitive(target.result)
    target.error = redact_text(target.error)


def _redact_event(_mapper: object, _connection: object, target: Any) -> None:
    target.payload = redact_sensitive(target.payload)


def _redact_task(_mapper: object, _connection: object, target: Any) -> None:
    target.input_payload = redact_sensitive(target.input_payload)
    target.output_payload = redact_sensitive(target.output_payload)
    target.error = redact_text(target.error)


def _redact_execution(_mapper: object, _connection: object, target: Any) -> None:
    target.execution_input = redact_sensitive(target.execution_input)
    target.execution_output = redact_sensitive(target.execution_output)
    target.error = redact_text(target.error)
