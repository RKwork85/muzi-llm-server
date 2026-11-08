"""Chat 和 AgentRuntime 共用的 MCP 执行入口。用户身份只来自服务端上下文。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass

from app.mcp.observe import breaker_for, mcp_log, metrics, observe_tool, redact

CURRENT_MCP_SCOPE: ContextVar["McpScope | None"] = ContextVar("mcp_scope", default=None)
_MAX_RESULT = 4000
_USER_LIMIT = 4
_GATES: dict[str, asyncio.Semaphore] = {}
_GATE_LOCK = asyncio.Lock()


@dataclass(frozen=True)
class McpScope:
    user_id: str
    request_id: str
    conversation_id: str | None = None
    agent_run_id: str | None = None


@dataclass(frozen=True)
class MCPExecutionContext:
    user_id: str
    connector_id: str
    tool_name: str
    local_name: str
    request_id: str
    timeout: float
    conversation_id: str | None = None
    agent_run_id: str | None = None
    permission_scope: str = "execute"
    idempotent: bool = True


def split_local(local_name: str) -> tuple[str, str] | None:
    parts = local_name.split("__")
    if len(parts) != 3 or parts[0] != "mcp" or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def context_for(local_name: str, *, timeout: float, idempotent: bool) -> MCPExecutionContext | None:
    """从服务端上下文和工具名生成上下文。忽略模型参数里的用户或连接器。"""
    scope = CURRENT_MCP_SCOPE.get()
    parsed = split_local(local_name)
    if scope is None or parsed is None:
        return None
    connector_id, tool_name = parsed
    return MCPExecutionContext(
        user_id=scope.user_id,
        connector_id=connector_id,
        tool_name=tool_name,
        local_name=local_name,
        request_id=scope.request_id,
        timeout=timeout,
        conversation_id=scope.conversation_id,
        agent_run_id=scope.agent_run_id,
        idempotent=idempotent,
    )


async def run_mcp(
    ctx: MCPExecutionContext,
    allowed: Callable[[MCPExecutionContext], Awaitable[bool]],
    call: Callable[[], Awaitable[str]],
) -> str:
    mcp_log("mcp.tool.created", **_fields(ctx, status="created"))
    if not breaker_for(ctx.connector_id).allow():
        _deny(ctx, "breaker_open")
        raise PermissionError("mcp user denied")
    permitted = await allowed(ctx)
    if not permitted or ctx.permission_scope != "execute":
        _deny(ctx, "denied")
        raise PermissionError("mcp user denied")
    mcp_log("mcp.tool.authorized", **_fields(ctx, status="authorized"))
    async with await _user_gate(ctx.user_id):
        return await _attempt(ctx, call)


async def _attempt(
    ctx: MCPExecutionContext,
    call: Callable[[], Awaitable[str]],
) -> str:
    mcp_log("mcp.tool.executing", **_fields(ctx, status="executing"))
    attempt = 0
    while True:
        attempt += 1
        metrics.bump("mcp_active_calls", 1)
        try:
            with observe_tool(ctx.connector_id):
                raw = await asyncio.wait_for(call(), ctx.timeout)
            breaker_for(ctx.connector_id).success()
            text = _limit(raw)
            mcp_log("mcp.tool.completed", **_fields(ctx, status="success"))
            return text
        except TimeoutError:
            metrics.add("mcp_tool_errors_total")
            breaker_for(ctx.connector_id).failure()
            if attempt == 1 and ctx.idempotent:
                metrics.add("mcp_retry_total")
                mcp_log("mcp.tool.retry", **_fields(ctx, status="retry"))
                continue
            mcp_log("mcp.tool.timeout", **_fields(ctx, status="timeout" if ctx.idempotent else "unknown"))
            raise
        except asyncio.CancelledError:
            status = "cancelled" if ctx.idempotent or attempt == 0 else "unknown"
            mcp_log("mcp.tool.cancelled", **_fields(ctx, status=status))
            raise
        except PermissionError:
            raise
        except Exception:
            metrics.add("mcp_tool_errors_total")
            breaker_for(ctx.connector_id).failure()
            if attempt == 1 and ctx.idempotent:
                metrics.add("mcp_retry_total")
                mcp_log("mcp.tool.retry", **_fields(ctx, status="retry"))
                continue
            mcp_log("mcp.tool.failed", **_fields(ctx, status="failed" if ctx.idempotent else "unknown"))
            raise
        finally:
            metrics.bump("mcp_active_calls", -1)


async def _user_gate(user_id: str) -> asyncio.Semaphore:
    async with _GATE_LOCK:
        gate = _GATES.get(user_id)
        if gate is None:
            gate = asyncio.Semaphore(_USER_LIMIT)
            _GATES[user_id] = gate
        return gate


def _deny(ctx: MCPExecutionContext, status: str) -> None:
    metrics.add("mcp_auth_denied_total")
    metrics.add("mcp_authorization_denied_total")
    mcp_log("mcp.auth.denied", **_fields(ctx, status=status))


def _limit(raw: str) -> str:
    if len(raw) <= _MAX_RESULT:
        return raw
    return raw[:_MAX_RESULT] + "...[truncated]"


def _fields(ctx: MCPExecutionContext, *, status: str) -> dict[str, str]:
    return redact(
        {
            "user_id": ctx.user_id,
            "connector_id": ctx.connector_id,
            "tool_name": ctx.tool_name,
            "request_id": ctx.request_id,
            "conversation_id": ctx.conversation_id or "",
            "agent_run_id": ctx.agent_run_id or "",
            "status": status,
        }
    )
