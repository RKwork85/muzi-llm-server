"""工具的原子操作能力声明。

这里是工具与权限之间的**唯一**映射：`access` → 原子动作（action）。
历史上那张 `AGENT_GRANT_BY_TOOL`（工具名 → 请求 grant 字符串）已删除，
因为它让同一个 access 的工具走两条不同的判定路径。

约定：
- `action` 为空表示该工具没有额外的操作权限要求，只需能进入当前模式。
- 只有工作区类动作（write/delete/execute/deliver）在 agent 模式下才要求
  工作区归属；读取类动作不要求。
- MCP 与 Skill 的权限粒度不同于工作区动作，使用不带模式前缀的权限码。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Scope(str, Enum):
    """工具执行时需要的资源作用域。"""

    NONE = "none"
    WORKSPACE = "workspace"
    MCP_CONNECTOR = "mcp_connector"
    SKILL_PACKAGE = "skill_package"


# 工作区类原子动作。这些动作按 `mode.action` 判定权限码。
WORKSPACE_READ = "workspace.read"
WORKSPACE_WRITE = "workspace.write"
WORKSPACE_DELETE = "workspace.delete"
COMMAND_EXECUTE = "command.execute"
FILE_DELIVER = "file.deliver"

MODE_SCOPED_ACTIONS = frozenset(
    {
        WORKSPACE_READ,
        WORKSPACE_WRITE,
        WORKSPACE_DELETE,
        COMMAND_EXECUTE,
        FILE_DELIVER,
    }
)

# 高风险动作：成功后也要留审计。
MUTATING_ACTIONS = frozenset(
    {
        WORKSPACE_WRITE,
        WORKSPACE_DELETE,
        COMMAND_EXECUTE,
        FILE_DELIVER,
    }
)

# agent 模式下需要校验工作区归属的动作。读取不受限，与旧行为一致。
WORKSPACE_OWNERSHIP_ACTIONS = MUTATING_ACTIONS

MCP_EXECUTE = "mcp.execute"
SKILL_EXECUTE = "skill.execute"

KNOWN_ACTIONS = MODE_SCOPED_ACTIONS | frozenset({MCP_EXECUTE, SKILL_EXECUTE})

# 进入某个模式本身所需的权限。没有额外动作要求的工具只校验这一项。
MODE_ENTRY_PERMISSION: dict[str, str] = {
    "ask": "chat.access",
    "plan": "chat.access",
    "agent": "agent.execute",
}


def entry_permission(mode: str) -> str:
    return MODE_ENTRY_PERMISSION.get(mode, "chat.access")


@dataclass(frozen=True)
class Capability:
    """一个工具声明自己需要什么操作权限、作用于哪种资源。"""

    action: str = ""
    scope: Scope = Scope.NONE
    audit: bool = False

    @property
    def gated(self) -> bool:
        return bool(self.action)


_ACTION_BY_ACCESS: dict[str, str] = {
    "read_files": WORKSPACE_READ,
    "write_workspace": WORKSPACE_WRITE,
    "delete_workspace": WORKSPACE_DELETE,
    "sandbox_exec": COMMAND_EXECUTE,
    # 交付/外发不是「写工作区」：它生成对外可访问的链接，风险不同，单独授权。
    "write_remote": FILE_DELIVER,
    "mutate_remote": WORKSPACE_WRITE,
    # 数据类工具沿用模式入口权限（agent.execute / chat.access），不再新增权限码。
    "write_data": "",
    "read_network": "",
    "read_local": "",
}

_SCOPE_BY_ACTION: dict[str, Scope] = {
    WORKSPACE_READ: Scope.WORKSPACE,
    WORKSPACE_WRITE: Scope.WORKSPACE,
    WORKSPACE_DELETE: Scope.WORKSPACE,
    COMMAND_EXECUTE: Scope.WORKSPACE,
    FILE_DELIVER: Scope.WORKSPACE,
}


def capability_for(access: str, toolset: str) -> Capability:
    """把工具注册时的 access/toolset 规范化成能力声明。

    只有三条工具集级别的规则（MCP、技能、技能脚本），其余一律由 access 决定。
    """
    if toolset == "mcp":
        return Capability(action=MCP_EXECUTE, scope=Scope.MCP_CONNECTOR)
    if toolset == "skills":
        if access == "sandbox_exec":
            # 技能脚本在沙盒里跑任意代码，门槛与 run_python 一致。
            return Capability(action=COMMAND_EXECUTE, scope=Scope.SKILL_PACKAGE, audit=True)
        return Capability(action=SKILL_EXECUTE, scope=Scope.SKILL_PACKAGE)
    action = _ACTION_BY_ACCESS.get(access, "")
    return Capability(
        action=action,
        scope=_SCOPE_BY_ACTION.get(action, Scope.NONE),
        audit=action in MUTATING_ACTIONS,
    )


def mode_permission(action: str, mode: str) -> str:
    """把原子动作换算成当前模式下的权限码。

    工作区类动作按 `mode.resource.action` 判定；MCP/Skill 使用不带模式的权限码。
    """
    if action in MODE_SCOPED_ACTIONS:
        return f"{mode}.{action}"
    return action
