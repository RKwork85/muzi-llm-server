"""把模式与工具集解析成本次允许的工具名单。

名单只来自 `visible_tools`：工具是否出现，由工具自己的能力声明、用户有效权限、
模式白名单和资源作用域共同决定。请求里的 `toolsets` 只能收窄，不能扩大。
"""

from __future__ import annotations

from collections.abc import Collection

from app.authorization.application.execution_guard import CURRENT_EXECUTION
from app.authorization.application.visibility import toolset_available, visible_tools
from app.agents.policy import PolicyError
from app.core.config import Settings
from app.tools.capabilities import BASELINE_EXCLUDED_TOOLSETS, MODE_TOOL_NAMES
from app.tools.registry import KNOWN_TOOLSETS, ToolRegistry


def ask_tool_names(
    registry: ToolRegistry,
    settings: Settings,
    user_id: str | None = None,
    *,
    permissions: Collection[str] | None = None,
    include_mcp: bool = False,
) -> tuple[str, ...]:
    """Ask 允许的系统工具。不包含删除、命令和多步骤编排。

    `include_mcp=True` 时把已授权连接器的工具一并纳入：Ask 是用户直接找 Notion/飞书
    办事的入口，插件面板里挂出来的能力必须真的能调用。
    """
    return visible_tools(
        registry,
        settings,
        mode="ask",
        permissions=permissions,
        mcp_user_id=user_id,
        offered=MODE_TOOL_NAMES["ask"],
        include_mcp=include_mcp,
    ).allowed


def plan_tool_names(
    registry: ToolRegistry,
    settings: Settings,
    *,
    permissions: Collection[str] | None = None,
) -> tuple[str, ...]:
    """Plan 只保留只读系统工具，不能写文件或调用外部应用。"""
    return visible_tools(
        registry,
        settings,
        mode="plan",
        permissions=permissions,
        offered=MODE_TOOL_NAMES["plan"],
    ).allowed


def agent_chat_tool_names(
    registry: ToolRegistry,
    settings: Settings,
    user_id: str | None = None,
    *,
    permissions: Collection[str] | None = None,
    workspace_scope: str | None = None,
    owned_scopes: Collection[str] | None = None,
) -> tuple[str, ...]:
    """对话里的 Agent 工具。与 AgentRuntime 使用同一套能力投影。"""
    return visible_tools(
        registry,
        settings,
        mode="agent",
        permissions=permissions,
        workspace_scope=workspace_scope,
        owned_scopes=owned_scopes,
        mcp_user_id=user_id,
        offered=MODE_TOOL_NAMES["agent"],
        include_mcp=True,
    ).allowed


def chat_mode_tool_names(
    registry: ToolRegistry,
    settings: Settings,
    mode: str,
    user_id: str | None = None,
    *,
    permissions: Collection[str] | None = None,
    workspace_scope: str | None = None,
    owned_scopes: Collection[str] | None = None,
) -> tuple[str, ...]:
    """对话通道按模式取工具。权限快照由调用方传入，缺省时读执行上下文。"""
    if mode == "plan":
        # Plan 模式已收敛到交互式规划子系统（app/planning）：对话通道不再挂载工具。
        # 保留枚举只为兼容旧客户端，返回空名单，避免任何写工具被误注入。
        return ()
    if mode == "agent":
        return agent_chat_tool_names(
            registry,
            settings,
            user_id,
            permissions=permissions,
            workspace_scope=workspace_scope,
            owned_scopes=owned_scopes,
        )
    return ask_tool_names(
        registry,
        settings,
        user_id,
        permissions=permissions,
        include_mcp=True,
    )


def agent_tool_names(
    registry: ToolRegistry,
    settings: Settings,
    toolsets: list[str] | None,
    user_id: str | None = None,
    permissions: Collection[str] | None = None,
    workspace_scope: str | None = None,
    owned_scopes: Collection[str] | None = None,
) -> tuple[str, ...]:
    """AgentRuntime 的工具名单。

    - toolsets 为 None：使用 Agent 默认基线（模式白名单，去掉图表工具）。
    - toolsets 有值：在该工具集内取用户所有可用工具；未授权的工具不会出现。

    旧请求字段 `grants` 已移除：工具一旦通过能力校验就代表它被授权，
    不需要客户端再单独声明一次「我要写文件」。
    """
    if toolsets is None:
        visibility = visible_tools(
            registry,
            settings,
            mode="agent",
            permissions=permissions,
            workspace_scope=workspace_scope,
            owned_scopes=owned_scopes,
            offered=MODE_TOOL_NAMES["agent"],
        )
        names = [
            name
            for name in visibility.allowed
            if _toolset_of(registry, name) not in BASELINE_EXCLUDED_TOOLSETS
        ]
        return tuple(sorted(set(names)))

    if not toolsets:
        raise PolicyError("AGENT_POLICY_INVALID", "toolsets 不能为空")
    for name in toolsets:
        if name not in KNOWN_TOOLSETS:
            raise PolicyError("TOOLSET_UNKNOWN", "未知的工具集")
        if not toolset_available(name, settings):
            raise PolicyError("TOOLSET_UNAVAILABLE", "工具集当前不可用")
    selected = set(toolsets)
    visibility = visible_tools(
        registry,
        settings,
        mode="agent",
        permissions=permissions,
        workspace_scope=workspace_scope,
        owned_scopes=owned_scopes,
        mcp_user_id=user_id,
        include_mcp="mcp" in selected,
    )
    names = [
        name
        for name in visibility.allowed
        if _toolset_of(registry, name) in selected
    ]
    return tuple(sorted(set(names)))


def _toolset_of(registry: ToolRegistry, name: str) -> str:
    spec = registry.get(name)
    return spec.toolset if spec is not None else ""


def current_permissions() -> frozenset[str]:
    """当前执行上下文的权限快照；没有上下文时返回空集合（默认拒绝）。"""
    context = CURRENT_EXECUTION.get()
    return frozenset(context.effective_permissions) if context is not None else frozenset()
