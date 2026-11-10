"""权限目录与内置角色定义。

权限 code 采用 `resource.action` 命名，稳定且不依赖前端路由。
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


@dataclass(frozen=True)
class PermissionSpec:
    code: str
    name: str
    resource: str
    action: str
    description: str = ""


@dataclass(frozen=True)
class RoleSpec:
    code: str
    name: str
    description: str
    permissions: frozenset[str]


# ---------------------------------------------------------------------------
# 权限目录
# ---------------------------------------------------------------------------

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
)

PERMISSION_BY_CODE: dict[str, PermissionSpec] = {spec.code: spec for spec in PERMISSION_CATALOG}


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
        permissions=_BASE_MEMBER,
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
# 数据范围
# ---------------------------------------------------------------------------

DATA_SCOPES = ("self", "shared", "org", "all")
