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

from app.tools.capability import (
    Capability,
    MODE_SCOPED_ACTIONS,
    Scope,
    WORKSPACE_OWNERSHIP_ACTIONS,
    capability_for,
    entry_permission,
    mode_permission,
)

logger = logging.getLogger("muzi_llm")

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
    plan_id: str | None = None
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
    """执行期复核。工具的能力声明是唯一依据，不再按工具名做特例。"""

    def decide(
        self,
        *,
        tool_name: str,
        toolset: str,
        access: str,
        capability: Capability | None = None,
    ) -> ToolDecision:
        del tool_name
        context = CURRENT_EXECUTION.get()
        cap = capability if capability is not None else capability_for(access, toolset)
        mode = context.runtime_mode if context is not None else "ask"

        if not cap.action:
            # 没有操作权限要求的工具：只要求能进入当前模式。
            if context is None:
                return _passthrough()
            entry = entry_permission(mode)
            if not context.user_id:
                return _deny(entry, "identity_missing")
            if entry not in context.effective_permissions:
                return _deny(entry, "permission_denied")
            return _allow(entry, audit=False)

        permission = mode_permission(cap.action, mode)
        if context is None or not context.user_id:
            if cap.action in MODE_SCOPED_ACTIONS:
                # 工作区/命令/交付类动作必须有可信身份，缺失时不放行。
                return _deny(permission, "identity_missing")
            # MCP/Skill 由连接器与技能层各自授权，这里不替代它们。
            return _passthrough()
        if permission not in context.effective_permissions:
            return _deny(permission, "permission_denied")
        if (
            cap.scope is Scope.WORKSPACE
            and cap.action in WORKSPACE_OWNERSHIP_ACTIONS
            and mode == "agent"
            and not _workspace_owned(context)
        ):
            # agent 模式的写/删/执行/交付仍要求工作区归属；
            # ask 模式按用户 scope 隔离，无需 ResourceGrant 记录。
            return _deny(permission, "workspace_unverified")
        if cap.scope is Scope.MCP_CONNECTOR and not mcp_runtime_allowed():
            return _deny(permission, "permission_denied")
        return _allow(permission, audit=cap.audit)


def mcp_runtime_allowed() -> bool:
    """MCP 连接器授权之外的运行时权限。没有执行上下文时不代替 stdio 授权。"""
    context = CURRENT_EXECUTION.get()
    if context is None:
        return True
    return bool(context.user_id) and "mcp.execute" in context.effective_permissions


async def authorize_registered_tool(spec: object) -> ToolDecision:
    capability = getattr(spec, "capability", None)
    decision = ExecutionGuard().decide(
        tool_name=str(getattr(spec, "name", "")),
        toolset=str(getattr(spec, "toolset", "")),
        access=str(getattr(spec, "access", "")),
        capability=capability if isinstance(capability, Capability) else None,
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
    plan_id: str | None = None,
) -> ExecutionContext:
    """从授权服务读取权限。失败时返回空权限。

    合法的工作区 scope 会登记为该用户的 workspace 归属（首次使用即绑定）；
    非 32 位十六进制的密钥不会被写入所有权。
    """
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
                await _ensure_workspace_grant(session, user_id, workspace_scope)
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
        plan_id=plan_id,
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


async def _ensure_workspace_grant(session: object, user_id: str, workspace_scope: str | None) -> None:
    """首次使用某个合法工作区 scope 时，登记为该用户的 workspace 授权。

    只有 32 位十六进制 scope 才会写入；任意客户端密钥不会成为所有权。
    """
    if not is_user_scope(workspace_scope):
        return
    try:
        actor = uuid.UUID(str(user_id))
    except ValueError:
        return
    try:
        from app.models.rbac import ResourceGrant

        existing = await session.scalar(  # type: ignore[attr-defined]
            select(ResourceGrant.id).where(
                ResourceGrant.user_id == actor,
                ResourceGrant.resource_type == "workspace",
                ResourceGrant.resource_id == workspace_scope,
            )
        )
        if existing is not None:
            return
        session.add(  # type: ignore[attr-defined]
            ResourceGrant(
                user_id=actor,
                resource_type="workspace",
                resource_id=workspace_scope,
                grant_type="allow",
            )
        )
        await session.commit()  # type: ignore[attr-defined]
    except Exception:
        logger.info("workspace grant registration unavailable user_id=%s", user_id)
        try:
            await session.rollback()  # type: ignore[attr-defined]
        except Exception:
            pass


async def bind_workspace_grant(session: object, user_id: str, workspace_scope: str | None) -> None:
    """首次出示某个合法工作区 scope 时登记归属（幂等）。

    只读入口（例如能力投影）也需要它：否则用户会因为「还没跑过一次任务」而看到
    agent 的写类工具与插件被标成 `workspace_unverified`，与真实请求的判定不一致。
    """
    await _ensure_workspace_grant(session, user_id, workspace_scope)


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
