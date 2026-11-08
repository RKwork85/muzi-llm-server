"""把请求里的 toolset 名称解析成本次允许的工具。"""

from __future__ import annotations

from pathlib import Path

from app.agents.policy import PolicyError, basic_tool_names
from app.core.config import ROOT, Settings
from app.data.store import store_is_ready
from app.tools.capabilities import AGENT_GRANT_BY_TOOL, ASK_TOOLS, PLAN_TOOLS
from app.tools.mcp_client import mcp_is_available
from app.tools.registry import KNOWN_TOOLSETS, ToolRegistry, ToolSpec
from app.tools.stdio_access import stdio_access

_ALWAYS = frozenset({"basic", "utility", "diagram"})


def ask_tool_names(registry: ToolRegistry, settings: Settings) -> tuple[str, ...]:
    """Ask 允许的系统工具。不包含 MCP、删除、命令和多步骤编排。"""
    return _named_tools(registry, settings, ASK_TOOLS)


def plan_tool_names(registry: ToolRegistry, settings: Settings) -> tuple[str, ...]:
    """Plan 只保留只读系统工具，不能写文件或调用外部应用。"""
    return _named_tools(registry, settings, PLAN_TOOLS)


def agent_chat_tool_names(
    registry: ToolRegistry,
    settings: Settings,
    user_id: str | None = None,
) -> tuple[str, ...]:
    """对话里的 Agent 系统工具。stdio MCP 只加入该用户已有的授权。"""
    names = list(ask_tool_names(registry, settings))
    if user_id and _toolset_available("mcp", settings):
        for name in sorted(stdio_access.names_for(user_id)):
            spec = registry.get(name)
            if spec is not None and spec.toolset == "mcp" and spec.risk_level == "low":
                names.append(name)
    return tuple(sorted(set(names)))


def chat_mode_tool_names(
    registry: ToolRegistry,
    settings: Settings,
    mode: str,
    user_id: str | None = None,
) -> tuple[str, ...]:
    if mode == "plan":
        return plan_tool_names(registry, settings)
    if mode == "agent":
        return agent_chat_tool_names(registry, settings, user_id)
    return ask_tool_names(registry, settings)


def _named_tools(registry: ToolRegistry, settings: Settings, allowed: frozenset[str]) -> tuple[str, ...]:
    names = [
        spec.name
        for spec in registry.all_tools()
        if spec.name in allowed and spec.risk_level == "low" and _toolset_available(spec.toolset, settings)
    ]
    return tuple(sorted(names))


def agent_tool_names(
    registry: ToolRegistry,
    settings: Settings,
    toolsets: list[str] | None,
    grants: list[str] | None,
    user_id: str | None = None,
) -> tuple[str, ...]:
    """Agent 默认可使用共享只读能力。写入、删除和命令必须带用户授权。"""
    grant_set = set(grants or [])
    if toolsets is None:
        names = [
            spec.name
            for spec in registry.all_tools()
            if spec.name in ASK_TOOLS
            and spec.name not in AGENT_GRANT_BY_TOOL
            and spec.toolset != "diagram"
            and spec.risk_level == "low"
            and _toolset_available(spec.toolset, settings)
        ]
        selected = None
    else:
        names = list(resolve_tool_names(registry, settings, toolsets, user_id))
        selected = set(toolsets)
    for tool, grant in AGENT_GRANT_BY_TOOL.items():
        if grant not in grant_set:
            continue
        spec = registry.get(tool)
        if spec is None or spec.risk_level != "low":
            continue
        if selected is not None and spec.toolset not in selected:
            continue
        if not _toolset_available(spec.toolset, settings):
            continue
        names.append(tool)
    names = [name for name in names if _user_can_call(registry, name, user_id)]
    return tuple(sorted(set(names)))


def resolve_tool_names(
    registry: ToolRegistry,
    settings: Settings,
    toolsets: list[str] | None,
    user_id: str | None = None,
) -> tuple[str, ...]:
    if toolsets is None:
        return basic_tool_names(registry)
    if not toolsets:
        raise PolicyError("AGENT_POLICY_INVALID", "toolsets 不能为空")
    for name in toolsets:
        if name not in KNOWN_TOOLSETS:
            raise PolicyError("TOOLSET_UNKNOWN", "未知的工具集")
        if not _toolset_available(name, settings):
            raise PolicyError("TOOLSET_UNAVAILABLE", "工具集当前不可用")
    selected = set(toolsets)
    names = [
        spec.name
        for spec in registry.all_tools()
        if spec.risk_level == "low"
        and spec.name not in AGENT_GRANT_BY_TOOL
        and spec.toolset in selected
        and _user_can_call(registry, spec.name, user_id)
    ]
    return tuple(sorted(names))


def _user_can_call(registry: ToolRegistry, name: str, user_id: str | None) -> bool:
    spec: ToolSpec | None = registry.get(name)
    if spec is None or spec.toolset != "mcp":
        return True
    return stdio_access.allows(user_id, name)


def _toolset_available(name: str, settings: Settings) -> bool:
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
