"""工具可见性：把「用户权限 + 模式 + 资源」投影成一份工具名单。

这是名单计算的唯一入口。所有模式（ask / plan / agent）与两条运行通道都走
这里，保证一个不变量：

    工具出现在名单里  ⇒  执行期不会因为权限被拒绝

执行期仍然保留一次复核，但那是防快照失效（TOCTOU），失败原因是
`PERMISSION_REVOKED` / `WORKSPACE_UNVERIFIED`，不再是「名单里有权、执行时没权」。
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path

from app.authorization.application.execution_guard import CURRENT_EXECUTION
from app.core.config import ROOT, Settings
from app.data.store import store_is_ready
from app.tools.capabilities import MCP_MODES, MODE_TOOL_NAMES, PLANNING_TODO_TOOL
from app.tools.capability import (
    MUTATING_ACTIONS,
    Scope,
    entry_permission,
    mode_permission,
)
from app.tools.mcp_client import mcp_is_available
from app.tools.registry import ToolRegistry, ToolSpec
from app.tools.stdio_access import stdio_access
from app.tools.workspace_scope import CURRENT_WORKSPACE_SCOPE, is_user_scope


@dataclass(frozen=True)
class ToolVisibility:
    """一次名单计算的结果。denied 供前端展示「为什么不可用」。"""

    allowed: tuple[str, ...]
    denied: dict[str, str] = field(default_factory=dict)

    def __contains__(self, name: object) -> bool:
        return name in self.allowed

    def toolsets(self, registry: ToolRegistry) -> tuple[str, ...]:
        found = {spec.toolset for spec in (registry.get(name) for name in self.allowed) if spec is not None}
        return tuple(sorted(found))


def _plan_context_active() -> bool:
    """当前运行是否绑定了具体计划。清单工具只在这种运行里有意义。"""
    context = CURRENT_EXECUTION.get()
    return bool(context is not None and context.plan_id)


def required_permission(spec: ToolSpec, mode: str) -> str:
    """工具在当前模式下的权限码。没有动作要求的工具退化为模式入口权限。"""
    action = spec.capability.action if spec.capability is not None else ""
    if not action:
        return entry_permission(mode)
    return mode_permission(action, mode)


def visible_tools(
    registry: ToolRegistry,
    settings: Settings,
    *,
    mode: str,
    permissions: Collection[str] | None = None,
    workspace_scope: str | None = None,
    owned_scopes: Collection[str] | None = None,
    mcp_user_id: str | None = None,
    offered: Collection[str] | None = None,
    include_mcp: bool = False,
) -> ToolVisibility:
    """计算某用户在某模式下可以使用的工具。

    `offered` 是模式白名单；为 None 表示不按工具名收窄（由调用方自行按 toolset 过滤）。
    `permissions=None` 表示没有权限上下文（静态目录/测试），此时只按模式白名单
    与环境可用性过滤，不当作「有权限」。真实请求路径必须传入权限集合。
    """
    effective_mode = mode if mode in MODE_TOOL_NAMES else "ask"
    allowlist = frozenset(offered) if offered is not None else None
    perms = _resolve_permissions(permissions)
    scope = _resolve_scope(workspace_scope)
    owned = _resolve_owned(owned_scopes)
    mcp_user = mcp_user_id or _context_user_id()

    allowed: list[str] = []
    denied: dict[str, str] = {}
    for spec in registry.all_tools():
        reason = _blocked_reason(
            spec,
            mode=effective_mode,
            offered=allowlist,
            permissions=perms,
            scope=scope,
            owned=owned,
            mcp_user_id=mcp_user,
            include_mcp=include_mcp,
            settings=settings,
        )
        if reason is None:
            allowed.append(spec.name)
        else:
            denied[spec.name] = reason
    return ToolVisibility(tuple(sorted(allowed)), denied)


def _blocked_reason(
    spec: ToolSpec,
    *,
    mode: str,
    offered: frozenset[str] | None,
    permissions: frozenset[str] | None,
    scope: str | None,
    owned: frozenset[str],
    mcp_user_id: str | None,
    include_mcp: bool,
    settings: Settings,
) -> str | None:
    if spec.risk_level != "low":
        return "risk_unsupported"
    # 执行期清单只属于「由计划交接而来的运行」：没有 plan_id 时不投影给模型，
    # 否则普通 Agent 对话会看到一个永远失败的写工具。
    if spec.name == PLANNING_TODO_TOOL and not _plan_context_active():
        return "plan_context_missing"
    # 模式判定排在环境可用性之前：否则 ask 下的 MCP 工具会被报成 backend_unavailable
    # （"后端没配"），把排查方向带偏到部署。这里只回答"本模式提不提供"。
    if spec.toolset == "mcp":
        if not include_mcp or mode not in MCP_MODES:
            return "mode_not_offered"
    elif offered is not None and spec.name not in offered:
        return "mode_not_offered"
    if not toolset_available(spec.toolset, settings):
        return "backend_unavailable"

    capability = spec.capability
    if capability is None:
        return "capability_missing"
    # 连接器授权必须在权限投影之前判断：没有用户上下文时同样拒绝。
    if capability.scope is Scope.MCP_CONNECTOR:
        if not mcp_user_id or not stdio_access.allows(mcp_user_id, spec.name):
            return "connector_unauthorized"

    if permissions is None:
        # 没有权限上下文：只给静态目录，不做授权推断。
        return None

    required = required_permission(spec, mode)
    if required not in permissions:
        return "permission_missing"

    if (
        capability.scope is Scope.WORKSPACE
        and capability.action in MUTATING_ACTIONS
        and mode == "agent"
        and not _workspace_owned(scope, owned)
    ):
        return "workspace_unverified"

    return None


def toolset_available(name: str, settings: Settings) -> bool:
    if name in _ALWAYS:
        return True
    if name == "research":
        return settings.tools_network_enabled and settings.has_api_key()
    if name == "weather":
        return settings.tools_network_enabled
    if name == "workspace" or name == "code":
        return _workspace_ready(settings)
    if name == "delivery":
        return _workspace_ready(settings) and bool(settings.oss_upload_base_url.strip())
    if name == "data":
        return store_is_ready(settings)
    if name == "mcp":
        return settings.mcp_enabled and mcp_is_available()
    if name == "skills":
        return bool(settings.skills_root.strip())
    return False


_ALWAYS = frozenset({"basic", "utility", "diagram"})


def _workspace_ready(settings: Settings) -> bool:
    raw = settings.tools_workspace_root.strip()
    if not raw:
        return False
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    try:
        resolved = path.resolve()
    except OSError:
        return False
    if resolved == ROOT:
        return False
    return resolved.is_dir()


def _workspace_owned(scope: str | None, owned: frozenset[str]) -> bool:
    return bool(scope) and is_user_scope(scope) and scope in owned


def _resolve_permissions(permissions: Collection[str] | None) -> frozenset[str] | None:
    if permissions is not None:
        return frozenset(permissions)
    context = CURRENT_EXECUTION.get()
    if context is None:
        return None
    return frozenset(context.effective_permissions)


def _resolve_scope(workspace_scope: str | None) -> str | None:
    if workspace_scope is not None:
        return workspace_scope
    active = CURRENT_WORKSPACE_SCOPE.get()
    if active is not None:
        return active
    context = CURRENT_EXECUTION.get()
    return context.workspace_scope if context is not None else None


def _resolve_owned(owned_scopes: Collection[str] | None) -> frozenset[str]:
    if owned_scopes is not None:
        return frozenset(owned_scopes)
    context = CURRENT_EXECUTION.get()
    return frozenset(context.owned_workspace_scopes) if context is not None else frozenset()


def _context_user_id() -> str | None:
    context = CURRENT_EXECUTION.get()
    return context.user_id if context is not None else None
