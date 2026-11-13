"""工具执行前的服务端授权。

客户端 grants、模型参数和工具注册都不是执行许可。
缺少可信身份时，敏感工具拒绝；数据库不可用时不自动放行。
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.tools.workspace_scope import CURRENT_WORKSPACE_SCOPE, is_user_scope

logger = logging.getLogger("muzi_llm")

# 工具 access → (resource, action)。这些「工具执行类」access 统一按
# `mode.resource.action` 权限码授权，例如 ask.workspace.write。
ACCESS_TOOL_PERMISSION: dict[str, tuple[str, str]] = {
    "read_files": ("workspace", "read"),
    "write_workspace": ("workspace", "write"),
    "write_remote": ("workspace", "write"),
    "delete_workspace": ("workspace", "delete"),
    "sandbox_exec": ("command", "execute"),
    "mutate_remote": ("workspace", "write"),
}

SENSITIVE_ACCESS = frozenset({"write_workspace", "write_remote", "delete_workspace", "sandbox_exec", "mutate_remote"})
READ_ACCESS = frozenset({"read_files"})

_TOOL_MODES = frozenset({"ask", "plan", "agent"})


def mode_permission_code(mode: str, access: str) -> str | None:
    """把 access 映射成 `mode.resource.action` 权限码；非工具执行类 access 返回 None。"""
    pair = ACCESS_TOOL_PERMISSION.get(access)
    if pair is None:
        return None
    resource, action = pair
    normalized = mode if mode in _TOOL_MODES else "ask"
    return f"{normalized}.{resource}.{action}"


_REASON_CODE = {
    "identity_missing": "IDENTITY_REQUIRED",
    "permission_denied": "PERMISSION_DENIED",
    "workspace_unverified": "WORKSPACE_UNVERIFIED",
}


@dataclass(frozen=True)
class ExecutionContext:
    """一次运行的服务端授权快照。权限集合只能由授权服务写入。"""

    user_id: str
    runtime_mode: str
    run_id: str | None = None
    conversation_id: str | None = None
    workspace_scope: str | None = None
    effective_permissions: frozenset[str] = frozenset()
    owned_workspace_scopes: frozenset[str] = frozenset()
    authorization_source: str = "rbac"


CURRENT_EXECUTION: ContextVar[ExecutionContext | None] = ContextVar("tool_execution", default=None)


@dataclass(frozen=True)
class ToolDecision:
    allowed: bool
    permission: str | None
    reason: str
    audit: bool

    @property
    def error_code(self) -> str | None:
        if self.allowed:
            return None
        return _REASON_CODE.get(self.reason, "PERMISSION_DENIED")


class ExecutionGuard:
    """不读取工具参数，也不把缺失身份当成放行。"""

    def decide(self, *, tool_name: str, toolset: str, access: str) -> ToolDecision:
        del tool_name
        context = CURRENT_EXECUTION.get()

        if toolset == "mcp":
            if context is None:
                return _passthrough()
            if not context.user_id or "mcp.execute" not in context.effective_permissions:
                return _deny("mcp.execute", "permission_denied" if context.user_id else "identity_missing")
            return _allow("mcp.execute", audit=False)

        if toolset == "skills":
            if context is None:
                return _passthrough()
            if not context.user_id or "skill.execute" not in context.effective_permissions:
                return _deny("skill.execute", "permission_denied" if context.user_id else "identity_missing")
            return _allow("skill.execute", audit=False)

        if access in SENSITIVE_ACCESS:
            permission = mode_permission_code(context.runtime_mode if context else "ask", access)
            if context is None or not context.user_id:
                return _deny(permission, "identity_missing")
            if permission not in context.effective_permissions:
                return _deny(permission, "permission_denied")
            if context.runtime_mode == "agent" and not _workspace_owned(context):
                # agent 模式的写/删/执行工具仍需工作区归属校验；
                # ask 模式的工作区按用户 scope 隔离，无需 ResourceGrant 记录。
                return _deny(permission, "workspace_unverified")
            return _allow(permission, audit=True)

        if access in READ_ACCESS:
            permission = mode_permission_code(context.runtime_mode if context else "ask", access)
            if context is None:
                return _passthrough()
            if permission not in context.effective_permissions:
                return _deny(permission, "permission_denied")
            return _allow(permission, audit=False)

        if context is None:
            return _passthrough()
        mode_permission = "agent.execute" if context.runtime_mode == "agent" else "chat.access"
        if mode_permission not in context.effective_permissions:
            return _deny(mode_permission, "permission_denied")
        return _allow(mode_permission, audit=False)


def mcp_runtime_allowed() -> bool:
    """MCP 连接器授权之外的运行时权限。没有执行上下文时不代替 stdio 授权。"""
    context = CURRENT_EXECUTION.get()
    if context is None:
        return True
    return bool(context.user_id) and "mcp.execute" in context.effective_permissions


async def authorize_registered_tool(spec: object) -> ToolDecision:
    decision = ExecutionGuard().decide(
        tool_name=str(getattr(spec, "name", "")),
        toolset=str(getattr(spec, "toolset", "")),
        access=str(getattr(spec, "access", "")),
    )
    if decision.audit:
        await record_tool_audit(decision, str(getattr(spec, "name", "")))
    return decision


async def load_execution_context(
    user_id: str,
    *,
    runtime_mode: str,
    run_id: str | None = None,
    conversation_id: str | None = None,
    workspace_scope: str | None = None,
) -> ExecutionContext:
    """从授权服务读取权限。失败时返回空权限，不把客户端密钥写成所有权。"""
    permissions: set[str] = set()
    owned: frozenset[str] = frozenset()
    source = "unavailable"
    try:
        from app.core.database.session import get_session_factory

        factory = get_session_factory()
    except RuntimeError:
        factory = None
    if factory is not None:
        try:
            from app.authorization.application.authorization_service import AuthorizationService

            async with factory() as session:
                permissions = set(await AuthorizationService(session).get_effective_permissions(user_id))
                owned = await _owned_workspaces(session, user_id)
                source = "rbac"
        except (SQLAlchemyError, RuntimeError, AttributeError):
            logger.info("tool authorization unavailable user_id=%s", user_id)
            permissions = set()
            owned = frozenset()
            source = "unavailable"
    return ExecutionContext(
        user_id=str(user_id),
        runtime_mode=runtime_mode,
        run_id=run_id,
        conversation_id=conversation_id,
        workspace_scope=workspace_scope if is_user_scope(workspace_scope) else None,
        effective_permissions=frozenset(permissions),
        owned_workspace_scopes=owned,
        authorization_source=source,
    )


async def record_tool_audit(decision: ToolDecision, tool_name: str) -> None:
    """拒绝和高风险放行都留痕。审计失败不改变授权结果。"""
    context = CURRENT_EXECUTION.get()
    user_id = context.user_id if context is not None else None
    mode = context.runtime_mode if context is not None else None
    run_id = context.run_id if context is not None else None
    logger.info(
        "tool_authorization user_id=%s tool=%s mode=%s decision=%s reason=%s run_id=%s",
        user_id or "-",
        tool_name,
        mode or "-",
        "allow" if decision.allowed else "deny",
        decision.reason,
        run_id or "-",
    )
    if user_id is None:
        return
    try:
        actor = uuid.UUID(str(user_id))
    except ValueError:
        return
    try:
        from app.authorization.application.audit_service import AuditService
        from app.core.database.session import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            await AuditService(session).record(
                actor,
                "tool.execute",
                permission=decision.permission,
                resource_type="tool",
                resource_id=tool_name[:128],
                decision="allow" if decision.allowed else "deny",
                outcome="success" if decision.allowed else "denied",
                context={
                    "runtime_mode": mode,
                    "reason": decision.reason,
                    "run_id": run_id,
                },
            )
            await session.commit()
    except Exception:
        logger.info("tool authorization audit skipped tool=%s", tool_name)


async def _owned_workspaces(session: object, user_id: str) -> frozenset[str]:
    try:
        actor = uuid.UUID(str(user_id))
    except ValueError:
        return frozenset()
    try:
        from app.models.rbac import ResourceGrant

        rows = await session.scalars(  # type: ignore[attr-defined]
            select(ResourceGrant.resource_id).where(
                ResourceGrant.user_id == actor,
                ResourceGrant.resource_type == "workspace",
                ResourceGrant.grant_type == "allow",
                ResourceGrant.revoked_at.is_(None),
            )
        )
    except Exception:
        logger.info("workspace ownership unavailable user_id=%s", user_id)
        return frozenset()
    return frozenset(item for item in rows if is_user_scope(str(item)))


def _workspace_owned(context: ExecutionContext) -> bool:
    active = CURRENT_WORKSPACE_SCOPE.get()
    if active is None:
        active = context.workspace_scope
    if context.workspace_scope and active and context.workspace_scope != active:
        return False
    return bool(active) and is_user_scope(active) and active in context.owned_workspace_scopes


def _deny(permission: str | None, reason: str) -> ToolDecision:
    return ToolDecision(False, permission, reason, True)


def _allow(permission: str | None, *, audit: bool) -> ToolDecision:
    return ToolDecision(True, permission, "allowed", audit)


def _passthrough() -> ToolDecision:
    """没有执行上下文时，只放行后续仍受 Runtime 名单和 MCP 授权约束的非敏感工具。"""
    return ToolDecision(True, None, "unscoped", False)
