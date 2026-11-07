"""把请求里的 toolset 名称解析成本次允许的工具。"""

from __future__ import annotations

from pathlib import Path

from app.agents.policy import PolicyError, basic_tool_names
from app.core.config import ROOT, Settings
from app.data.store import store_is_ready
from app.tools.capabilities import AGENT_GRANT_BY_TOOL, ASK_TOOLS
from app.tools.registry import KNOWN_TOOLSETS, ToolRegistry

_ALWAYS = frozenset({"basic", "utility", "diagram"})


def ask_tool_names(registry: ToolRegistry, settings: Settings) -> tuple[str, ...]:
    """Ask 允许的工具。不包含删除、命令和多步骤编排。"""
    names = [
        spec.name
        for spec in registry.all_tools()
        if spec.name in ASK_TOOLS
        and spec.risk_level == "low"
        and _toolset_available(spec.toolset, settings)
    ]
    return tuple(sorted(names))


def agent_tool_names(
    registry: ToolRegistry,
    settings: Settings,
    toolsets: list[str] | None,
    grants: list[str] | None,
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
        names = list(resolve_tool_names(registry, settings, toolsets))
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
    return tuple(sorted(set(names)))


def resolve_tool_names(
    registry: ToolRegistry,
    settings: Settings,
    toolsets: list[str] | None,
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
    ]
    return tuple(sorted(names))


def _toolset_available(name: str, settings: Settings) -> bool:
    if name in _ALWAYS:
        return True
    if name == "research":
        return settings.tools_network_enabled and settings.has_api_key()
    if name == "weather":
        return settings.tools_network_enabled
    if name == "workspace":
        return _workspace_ready(settings)
    if name == "delivery":
        return _workspace_ready(settings) and bool(settings.oss_upload_base_url.strip())
    if name == "data":
        return store_is_ready(settings)
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
