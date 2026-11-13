"""权限目录与内置角色定义。

权限 code 分两类：
- 功能权限：`resource.action`，与运行时无关（如 chat.access、user.read）。
- 工具执行权限：`mode.resource.action`，按工作模式（ask/plan/agent）区分，
  例如 `ask.workspace.write` 表示「普通对话里写入工作区」。

角色只是权限集合；业务代码通过 AuthorizationService.has_permission 判断，
不直接写 if role == 'admin'。

data_scope 说明：
- SELF：只能访问自己的数据
- SHARED：自己的数据 + 被授权共享的数据
- ALL：全平台数据（超级管理员/管理员）
- ORG：所属组织数据（预留，暂未启用）
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 内置角色
# ---------------------------------------------------------------------------

SUPER_ADMIN_ROLE_CODE = "super_admin"
ADMIN_ROLE_CODE = "admin"
DEVELOPER_ROLE_CODE = "developer"
MEMBER_ROLE_CODE = "member"
GUEST_ROLE_CODE = "guest"

# ---------------------------------------------------------------------------
# 工作模式
# ---------------------------------------------------------------------------

# 工具执行权限按这三个模式拆分。功能权限 mode 为空字符串。
TOOL_MODES: tuple[str, ...] = ("ask", "plan", "agent")


@dataclass(frozen=True)
class PermissionSpec:
    code: str
    name: str
    resource: str
    action: str
    description: str = ""
    mode: str = ""  # 工具执行权限的模式前缀；功能权限为空


@dataclass(frozen=True)
class RoleSpec:
    code: str
    name: str
    description: str
    permissions: frozenset[str]


# ---------------------------------------------------------------------------
# 权限目录
# ---------------------------------------------------------------------------

# 工具执行权限的 (resource, action, 名称)。按 TOOL_MODES 展开为 mode.resource.action。
_TOOL_ACTIONS: tuple[tuple[str, str, str], ...] = (
    ("workspace", "read", "读取工作区"),
    ("workspace", "write", "写入工作区"),
    ("workspace", "delete", "删除工作区文件"),
    ("command", "execute", "执行命令"),
)


def _tool_permission_specs() -> list[PermissionSpec]:
    specs: list[PermissionSpec] = []
    for mode in TOOL_MODES:
        for resource, action, label in _TOOL_ACTIONS:
            specs.append(
                PermissionSpec(
                    code=f"{mode}.{resource}.{action}",
                    name=label,
                    resource=resource,
                    action=action,
                    mode=mode,
                )
            )
    return specs


PERMISSION_CATALOG: tuple[PermissionSpec, ...] = (
    # 功能权限
    PermissionSpec("chat.access", "使用对话", "chat", "access", "使用基础对话能力"),
    PermissionSpec("agent.access", "使用 Agent", "agent", "access", "查看和使用 Agent"),
    PermissionSpec("agent.create", "创建 Agent", "agent", "create", "创建个人 Agent"),
    PermissionSpec("agent.execute", "执行 Agent", "agent", "execute", "发起 Agent 运行"),
    PermissionSpec("agent.delete", "删除 Agent", "agent", "delete", "删除 Agent"),
    PermissionSpec("agent.share", "分享 Agent", "agent", "share", "把 Agent 分享给其他用户"),
    PermissionSpec("skill.access", "使用 Skill", "skill", "access", "查看可用技能"),
    PermissionSpec("skill.install", "安装 Skill", "skill", "install", "安装技能包"),
    PermissionSpec("skill.execute", "执行 Skill", "skill", "execute", "执行技能指令"),
    PermissionSpec("mcp.access", "使用 MCP", "mcp", "access", "使用远程 MCP 能力"),
    PermissionSpec("mcp.connect", "连接 MCP", "mcp", "connect", "建立 MCP 授权连接"),
    PermissionSpec("mcp.execute", "执行 MCP 工具", "mcp", "execute", "调用 MCP 工具"),
    PermissionSpec("plugin.access", "使用插件", "plugin", "access", "启用和使用插件"),
    PermissionSpec("model.access", "使用模型", "model", "access", "调用大模型"),
    PermissionSpec("knowledge.access", "使用知识库", "knowledge", "access", "查询知识库"),
    PermissionSpec("admin.access", "进入管理后台", "admin", "access", "访问管理后台"),
    # 用户管理
    PermissionSpec("user.read", "查看用户", "user", "read", "查看用户列表与详情"),
    PermissionSpec("user.create", "创建用户", "user", "create", "管理员创建用户"),
    PermissionSpec("user.update", "修改用户", "user", "update", "修改用户基本信息"),
    PermissionSpec("user.delete", "删除用户", "user", "delete", "删除用户"),
    PermissionSpec("user.manage", "管理用户", "user", "manage", "封禁/解封用户"),
    # 角色与权限
    PermissionSpec("role.read", "查看角色", "role", "read", "查看角色定义"),
    PermissionSpec("role.manage", "管理角色", "role", "manage", "创建、修改、删除角色"),
    PermissionSpec("permission.read", "查看权限", "permission", "read", "查看权限字典"),
    PermissionSpec("permission.manage", "管理权限", "permission", "manage", "管理权限字典与授权策略"),
    # 任务与审计
    PermissionSpec("task.read", "查看任务", "task", "read", "查看任务记录"),
    PermissionSpec("task.cancel", "取消任务", "task", "cancel", "取消运行中的任务"),
    PermissionSpec("audit.read", "查看审计日志", "audit", "read", "查看审计日志"),
    # 额度
    PermissionSpec("quota.read", "查看额度", "quota", "read", "查看使用额度"),
    PermissionSpec("quota.manage", "管理额度", "quota", "manage", "调整用户额度"),
    # 工具执行权限（按模式展开）
    *_tool_permission_specs(),
)

PERMISSION_BY_CODE: dict[str, PermissionSpec] = {spec.code: spec for spec in PERMISSION_CATALOG}

# 旧的、无模式前缀的工具执行权限码（数据迁移时清理用）。
LEGACY_TOOL_PERMISSION_CODES = frozenset(
    {
        "workspace.read",
        "workspace.write",
        "workspace.delete",
        "command.execute",
    }
)


def _p(*codes: str) -> frozenset[str]:
    return frozenset(codes)


# ---------------------------------------------------------------------------
# 内置角色权限矩阵
#
# super_admin 不在 role_permissions 里显式列出，授权服务对其隐式放行全部权限，
# 避免超级管理员通过普通角色配置被误删权限或降级。
# ---------------------------------------------------------------------------

# 所有非 super_admin 角色共享的基础功能权限
_BASE_MEMBER = _p(
    "chat.access",
    "agent.access",
    "agent.create",
    "agent.execute",
    "skill.access",
    "skill.execute",
    "mcp.access",
    "model.access",
    "knowledge.access",
    "task.read",
    "task.cancel",
)

# ---------------------------------------------------------------------------
# 工具执行权限的默认角色许可（按模式）
#
# 普通对话（ask）是常规能力：member 即可读写工作区（生成文档/写文件），
# 但删除与执行代码仍是高风险，只给 developer 及以上。
# plan 保持只读：任何内置角色都不授予写/删/执行。
# agent 模式更敏感：写/删/执行只给 developer 及以上，member 仅可读。
# ---------------------------------------------------------------------------
_TOOL_GRANT_MEMBER = _p(
    "ask.workspace.read",
    "ask.workspace.write",
    "plan.workspace.read",
    "agent.workspace.read",
)

_TOOL_GRANT_DEVELOPER = _TOOL_GRANT_MEMBER | _p(
    "ask.workspace.delete",
    "ask.command.execute",
    "agent.workspace.write",
    "agent.workspace.delete",
    "agent.command.execute",
)

BUILTIN_ROLES: tuple[RoleSpec, ...] = (
    RoleSpec(
        code=SUPER_ADMIN_ROLE_CODE,
        name="超级管理员",
        description="平台最高权限，管理整个平台、用户、角色、权限和系统配置。",
        permissions=frozenset(),  # 隐式全部权限
    ),
    RoleSpec(
        code=ADMIN_ROLE_CODE,
        name="平台管理员",
        description="运营管理员，管理用户、资源、审核和运营配置。",
        permissions=_BASE_MEMBER
        | _TOOL_GRANT_DEVELOPER
        | _p(
            "agent.delete",
            "agent.share",
            "skill.install",
            "mcp.connect",
            "mcp.execute",
            "plugin.access",
            "admin.access",
            "user.read",
            "user.create",
            "user.update",
            "user.delete",
            "user.manage",
            "role.read",
            "role.manage",
            "permission.read",
            "permission.manage",
            "audit.read",
            "quota.read",
            "quota.manage",
        ),
    ),
    RoleSpec(
        code=DEVELOPER_ROLE_CODE,
        name="开发者",
        description="高级用户，使用高级 Agent、Tool、Skill 和开发能力。",
        permissions=_BASE_MEMBER
        | _TOOL_GRANT_DEVELOPER
        | _p(
            "agent.delete",
            "agent.share",
            "skill.install",
            "mcp.connect",
            "mcp.execute",
            "plugin.access",
        ),
    ),
    RoleSpec(
        code=MEMBER_ROLE_CODE,
        name="普通用户",
        description="标准用户，使用普通对话、个人 Agent 和授权资源。",
        permissions=_BASE_MEMBER | _TOOL_GRANT_MEMBER,
    ),
    RoleSpec(
        code=GUEST_ROLE_CODE,
        name="受限访客",
        description="受限用户，仅开放基础对话功能。",
        permissions=_p("chat.access"),
    ),
)

ROLE_BY_CODE: dict[str, RoleSpec] = {spec.code: spec for spec in BUILTIN_ROLES}


# ---------------------------------------------------------------------------
# 角色等级（分级控制）
# ---------------------------------------------------------------------------
# 数值越大等级越高。拥有 quota.manage 等管理权限的角色，只能对「等级 ≤ 自己」
# 的目标用户执行操作：super_admin 可控制所有角色，admin 可控制 admin 及以下，
# 逐级向下。自定义角色未在此表列出，视为最低等级，默认只能控制同等级或更低的目标。
ROLE_RANK: dict[str, int] = {
    SUPER_ADMIN_ROLE_CODE: 100,
    ADMIN_ROLE_CODE: 80,
    DEVELOPER_ROLE_CODE: 60,
    MEMBER_ROLE_CODE: 40,
    GUEST_ROLE_CODE: 20,
}
DEFAULT_ROLE_RANK = 0


def max_role_rank(role_codes: list[str] | tuple[str, ...] | set[str]) -> int:
    """取一组角色 code 的最高等级。未知角色按 DEFAULT_ROLE_RANK 处理。"""
    return max((ROLE_RANK.get(code, DEFAULT_ROLE_RANK) for code in role_codes), default=DEFAULT_ROLE_RANK)


def role_grant_rank(role_code: str) -> int:
    """角色可被授予的权限等级上限。

    用于后台「角色权限」面板的可配置范围：只有等级足够高的角色，才能被配置
    对应等级的权限。自定义角色未在 ROLE_RANK 列出，按普通用户（member）等级处理，
    避免管理员无法为自定义角色配置基础权限。
    """
    return ROLE_RANK.get(role_code, ROLE_RANK[MEMBER_ROLE_CODE])


# ---------------------------------------------------------------------------
# 权限可授予的最小角色等级（可配置范围门槛）
# ---------------------------------------------------------------------------
# 数值来自 ROLE_RANK。低等级角色在后台看不到、也无法被授予超过其等级的权限，
# 防止普通用户（member）被错误地勾上「删除用户 / 创建用户」等管理权限。
# 未显式列出的权限默认门槛为 member 等级（40）。
PERMISSION_MIN_RANK: dict[str, int] = {
    # 仅管理员（80）可配置的管理类权限
    "admin.access": 80,
    "user.read": 80,
    "user.create": 80,
    "user.update": 80,
    "user.delete": 80,
    "user.manage": 80,
    "role.read": 80,
    "role.manage": 80,
    "permission.read": 80,
    "permission.manage": 80,
    "audit.read": 80,
    "quota.read": 80,
    "quota.manage": 80,
    # plan 模式默认只读；写/删/执行仅管理员可开放给自定义角色
    "plan.workspace.write": 80,
    "plan.workspace.delete": 80,
    "plan.command.execute": 80,
    # 开发者（60）及以上
    "agent.delete": 60,
    "agent.share": 60,
    "skill.install": 60,
    "mcp.connect": 60,
    "mcp.execute": 60,
    "plugin.access": 60,
    "ask.workspace.delete": 60,
    "ask.command.execute": 60,
    "agent.workspace.write": 60,
    "agent.workspace.delete": 60,
    "agent.command.execute": 60,
    # 访客（20）即可配置的基础对话
    "chat.access": 20,
}

DEFAULT_PERMISSION_MIN_RANK = ROLE_RANK[MEMBER_ROLE_CODE]


def permission_min_rank(code: str) -> int:
    """返回权限可授予的最小角色等级。未列出的默认按 member 等级处理。"""
    return PERMISSION_MIN_RANK.get(code, DEFAULT_PERMISSION_MIN_RANK)


# ---------------------------------------------------------------------------
# 角色默认额度（逐级递增）
# ---------------------------------------------------------------------------
# 各角色的默认赠送额度（total_tokens）。limit_value=0 表示不限量（无限额度）。
# 逐级递增：普通用户 member 100000，向上每级 +200000；超级管理员无限额度。
ROLE_QUOTA_LIMIT: dict[str, int] = {
    SUPER_ADMIN_ROLE_CODE: 0,  # 无限
    ADMIN_ROLE_CODE: 500_000,
    DEVELOPER_ROLE_CODE: 300_000,
    MEMBER_ROLE_CODE: 100_000,
    GUEST_ROLE_CODE: 100_000,
}
# 未匹配任何内置角色时的兜底默认额度。
DEFAULT_QUOTA_LIMIT = 100_000


def default_quota_for_roles(role_codes: list[str] | tuple[str, ...] | set[str]) -> int:
    """返回一组角色对应的默认额度，取等级最高角色对应的额度。

    超级管理员（等级最高）返回 0（无限）。未知角色回退到 DEFAULT_QUOTA_LIMIT。
    """
    if not role_codes:
        return DEFAULT_QUOTA_LIMIT
    best = max((c for c in role_codes if c in ROLE_QUOTA_LIMIT), key=lambda c: ROLE_RANK.get(c, 0), default=None)
    if best is None:
        return DEFAULT_QUOTA_LIMIT
    return ROLE_QUOTA_LIMIT[best]


# ---------------------------------------------------------------------------
# 数据范围
# ---------------------------------------------------------------------------

DATA_SCOPES = ("self", "shared", "org", "all")
