"""把请求里的 toolset 名称解析成本次允许的工具。"""

from __future__ import annotations

from pathlib import Path

from app.agents.policy import PolicyError, basic_tool_names
from app.core.config import ROOT, Settings
from app.data.store import store_is_ready
from app.tools.registry import KNOWN_TOOLSETS, ToolRegistry

_ALWAYS = frozenset({"basic", "utility", "diagram"})


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
        if spec.risk_level == "low" and spec.toolset in selected
    ]
    return tuple(sorted(names))


def _toolset_available(name: str, settings: Settings) -> bool:
    if name in _ALWAYS:
        return True
    if name == "research":
        return settings.tools_network_enabled and settings.has_api_key()
    if name == "workspace":
        return _workspace_ready(settings)
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
