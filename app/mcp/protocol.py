"""MCP 生命周期消息。只使用仍带 initialize 的官方版本，不发明字段。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# 这些版本都要求 initialize、notifications/initialized，以及后续请求使用协商后的协议版本。
# 2026-07-28 改为无状态，本客户端不实现，因此不放进支持列表。
SUPPORTED_PROTOCOL_VERSIONS = (
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)
PREFERRED_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]
MAX_TOOL_PAGES = 8

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603

_FATAL_REASONS = frozenset(
    {
        "protocol_version",
        "initialize_result",
        "session_invalid",
        "invalid_response",
    }
)


class ProtocolError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    @property
    def fatal(self) -> bool:
        return self.reason in _FATAL_REASONS


@dataclass(frozen=True)
class NegotiatedSession:
    protocol_version: str
    tools_list_changed: bool
    server_name: str


def client_initialize_params() -> dict[str, Any]:
    """客户端能力留空。不声明 roots、sampling、elicitation。"""
    return {
        "protocolVersion": PREFERRED_PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "muzi-llm", "version": "1"},
    }


def accept_initialize_result(result: object) -> NegotiatedSession:
    if not isinstance(result, dict):
        raise ProtocolError("initialize_result")
    version = result.get("protocolVersion")
    if not isinstance(version, str) or version not in SUPPORTED_PROTOCOL_VERSIONS:
        raise ProtocolError("protocol_version")
    capabilities = result.get("capabilities")
    tools_changed = False
    if isinstance(capabilities, dict):
        tools = capabilities.get("tools")
        if isinstance(tools, dict) and tools.get("listChanged") is True:
            tools_changed = True
    info = result.get("serverInfo")
    name = ""
    if isinstance(info, dict) and isinstance(info.get("name"), str):
        name = info["name"]
    return NegotiatedSession(version, tools_changed, name)


def initialized_notification() -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": "notifications/initialized"}


def cancelled_notification(request_id: int | str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "method": "notifications/cancelled",
        "params": {"requestId": request_id, "reason": "timeout"},
    }


def tools_list_params(cursor: str | None) -> dict[str, Any]:
    if cursor:
        return {"cursor": cursor}
    return {}


def parse_tools_page(result: object) -> tuple[list[dict[str, Any]], str | None]:
    if not isinstance(result, dict):
        raise ProtocolError("tools_result")
    raw = result.get("tools")
    if not isinstance(raw, list):
        raise ProtocolError("tools_result")
    tools = [item for item in raw if isinstance(item, dict)]
    cursor = result.get("nextCursor")
    if cursor is None or cursor == "":
        return tools, None
    if not isinstance(cursor, str):
        raise ProtocolError("tools_cursor")
    return tools, cursor


def server_reply(message: dict[str, Any]) -> dict[str, Any] | None:
    """回应服务端发来的请求。通知没有 id，返回 None。"""
    method = message.get("method")
    if not isinstance(method, str) or "id" not in message:
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": message["id"], "result": {}}
    return {
        "jsonrpc": "2.0",
        "id": message["id"],
        "error": {"code": JSONRPC_METHOD_NOT_FOUND, "message": "Method not found"},
    }


def notification_effect(message: dict[str, Any]) -> str:
    method = message.get("method")
    if not isinstance(method, str) or "id" in message:
        return ""
    if method == "notifications/tools/list_changed":
        return "tools_changed"
    return "ignore"


def error_reason(error: object) -> str:
    if not isinstance(error, dict):
        return "rpc_error"
    code = error.get("code")
    if code == JSONRPC_METHOD_NOT_FOUND:
        return "method_not_found"
    if code == JSONRPC_INVALID_PARAMS:
        return "invalid_params"
    if code == JSONRPC_INTERNAL_ERROR:
        return "internal"
    if code == JSONRPC_PARSE_ERROR:
        return "parse"
    if code == JSONRPC_INVALID_REQUEST:
        return "invalid_request"
    return "rpc_error"
