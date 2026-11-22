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

# 页面清单是「页面 → 浏览权限 / 该页功能」的唯一事实来源；这里再导出，
# 让 `from ...catalog import PAGE_ACCESS_CODES` 的老调用点继续成立。
from app.authorization.domain.pages import (  # noqa: F401  (再导出)
    PAGE_ACCESS_CODES,
    PAGE_CATALOG,
    PAGE_FEATURE_CODES,
    PAGE_BY_KEY,
    PageSpec,
)

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
# action 必须与 app/tools/capability.py 的原子动作一致。
_TOOL_ACTIONS: tuple[tuple[str, str, str], ...] = (
    ("workspace", "read", "读取工作区"),
    ("workspace", "write", "写入工作区"),
    ("workspace", "delete", "删除工作区文件"),
    ("command", "execute", "执行命令"),
    # 交付/外发会生成对外可访问的链接，与「写本地文件」风险不同，单独授权。
    ("file", "deliver", "交付文件（生成外发链接）"),
)

# 工具执行权限的 resource 集合。只有这些资源下的码才按模式（ask/plan/agent）展开，
# 供 API 层区分「工具执行权限」与「功能权限」——`agent.access` 属于后者，
# 不能因为首段恰好叫 agent 就被当成 Agent 模式下的内置工具。
TOOL_PERMISSION_RESOURCES: frozenset[str] = frozenset(
    resource for resource, _, _ in _TOOL_ACTIONS
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
    # ------------------------------------------------------------------
    # 页面浏览权限
    #
    # 与上面的 *.access 同一语义：决定「能不能进这个页面」。
    # 只在前端路由与导航层使用，不作为接口的安全边界——页面是前端产物，
    # 真正的数据安全由各接口的 user_id 隔离与写操作权限保证。
    #
    # 命名约束：首段不能用 ask/plan/agent，否则会被误认成工具执行权限。
    # ------------------------------------------------------------------
    PermissionSpec("history.access", "浏览历史记录", "history", "access", "进入历史记录页面"),
    PermissionSpec("assets.access", "浏览资产", "assets", "access", "进入资产中心页面"),
    PermissionSpec("creation.access", "浏览创作", "creation", "access", "进入创作板块与生成工作台"),
    PermissionSpec("flow.access", "浏览画布", "flow", "access", "进入创作画布工作台"),
    PermissionSpec("docs.access", "浏览文档空间", "docs", "access", "进入文档空间"),
    # ------------------------------------------------------------------
    # 功能使用权限（写操作）
    #
    # 全局资源维度，不与页面对应：同一个码可能被多个页面共用
    # （例如 flow.write 同时覆盖画布保存、删除与会话管理）。
    # 后台只按「影响哪些页面」展示，授权本身只有一个开关，避免按页面拆分导致权限放大。
    # ------------------------------------------------------------------
    PermissionSpec("chat.write", "对话写操作", "chat", "write", "发送消息、上传附件"),
    PermissionSpec("history.write", "历史写操作", "history", "write", "保存、重命名与删除历史记录"),
    PermissionSpec("content.write", "创作写操作", "content", "write", "发起采集、生成、发布与取消任务"),
    PermissionSpec("flow.write", "画布写操作", "flow", "write", "保存、删除画布与上传素材"),
    PermissionSpec("docs.write", "文档写操作", "docs", "write", "新建、编辑、删除文档与上传图片"),
    PermissionSpec("plugin.write", "插件安装", "plugin", "write", "安装与卸载插件"),
    PermissionSpec("profile.write", "资料写操作", "profile", "write", "修改用户名与头像"),
    PermissionSpec("recharge.write", "充值写操作", "recharge", "write", "创建充值订单"),
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


# 专用「页面浏览」权限：只为页面准入而存在，不承担任何接口授权。
#
# 清单在 pages.py（页面 → 浏览权限 + 该页功能），这里只做再导出。
# 不要在这里手写第二份页面清单或权限码：新增页面只改 pages.py 一处，
# 后台面板与路由守卫都会自动跟上。


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
    # 调用已按人授权的远端连接器（Notion / 企业微信 / 飞书）。
    # 连接器本身要用户自己完成 OAuth 或填凭据，`mcp.execute` 只决定「授权后能不能调」，
    # 所以它和 `mcp.access` 同级：member 也该能用，不然 ask 里挂出来的插件永远是空的。
    "mcp.execute",
    "model.access",
    "knowledge.access",
    # `/` 插件面板是一个独立的功能入口：面板里的条目会向模型承诺能力，
    # 所以由功能权限统一开关，再由各插件声明的工具依赖继续收窄。
    "plugin.access",
    "task.read",
    "task.cancel",
    # 页面浏览：登录用户的默认可见范围。除访客外的角色默认都能浏览全部页面，
    # 保持与「引入页面授权之前」完全一致的行为；要收窄某个角色，由管理员在后台取消勾选。
    "history.access",
    "assets.access",
    "creation.access",
    "flow.access",
    "docs.access",
    # 功能使用（写操作）：默认全部放开，理由同上——先保证行为不变。
    # 收窄同样靠后台取消勾选，不需要改代码。
    "chat.write",
    "history.write",
    "content.write",
    "flow.write",
    "docs.write",
    "plugin.write",
    "profile.write",
    "recharge.write",
)

# ---------------------------------------------------------------------------
# 工具授权矩阵
#
# 唯一一张「角色 × 模式 → 原子动作」的表。上面的权限码由 _TOOL_ACTIONS 生成，
# 角色默认许可由这张表生成，二者不会再各自漂移。
#
# 为什么权限码仍带模式前缀：member 在 ask 可以写，在 agent 不能。如果折叠成
# 不带模式的 `workspace.write`，member 会因为拥有该权限而顺带获得 agent 写权限，
# 属于权限放大。模式前缀是可授予单位，原子动作是工具侧的规范化键。
# ---------------------------------------------------------------------------
_TOOL_MATRIX: dict[str, dict[str, frozenset[str]]] = {
    MEMBER_ROLE_CODE: {
        "ask": _p("workspace.read", "workspace.write", "file.deliver"),
        "plan": _p("workspace.read"),
        "agent": _p("workspace.read", "file.deliver"),
    },
    DEVELOPER_ROLE_CODE: {
        "ask": _p(
            "workspace.read",
            "workspace.write",
            "workspace.delete",
            "command.execute",
            "file.deliver",
        ),
        "plan": _p("workspace.read"),
        "agent": _p(
            "workspace.read",
            "workspace.write",
            "workspace.delete",
            "command.execute",
            "file.deliver",
        ),
    },
}
_TOOL_MATRIX[ADMIN_ROLE_CODE] = _TOOL_MATRIX[DEVELOPER_ROLE_CODE]


def _tool_grants(role_code: str) -> frozenset[str]:
    modes = _TOOL_MATRIX.get(role_code, {})
    return frozenset(f"{mode}.{action}" for mode, actions in modes.items() for action in actions)


_TOOL_GRANT_MEMBER = _tool_grants(MEMBER_ROLE_CODE)
_TOOL_GRANT_DEVELOPER = _tool_grants(DEVELOPER_ROLE_CODE)
_TOOL_GRANT_ADMIN = _tool_grants(ADMIN_ROLE_CODE)

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
        | _TOOL_GRANT_ADMIN
        | _p(
            "agent.delete",
            "agent.share",
            "skill.install",
            "mcp.connect",
            "mcp.execute",
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
        name="受限用户",
        description="受限账号：只能进入对话与画布，其余页面与写操作需管理员另行开放。",
        # guest 是一个能挂在已登录账号上的最低档位（等级 20），
        # 用于试用、降级或人工收窄的账号；code 保留 guest 是为了不动数据库主键。
        permissions=_p("chat.access", "chat.write", "flow.access"),
    ),
)

ROLE_BY_CODE: dict[str, RoleSpec] = {spec.code: spec for spec in BUILTIN_ROLES}

# 页面浏览权限的后缀约定：`<resource>.access` 只回答「能不能进这个页面」。
PAGE_PERMISSION_SUFFIX = ".access"


def is_page_permission(code: str) -> bool:
    """是否页面浏览权限（`*.access`）。"""
    return code.endswith(PAGE_PERMISSION_SUFFIX)


# 内置角色用过的旧展示名。seed 只在「当前名字仍是旧默认值」时才改写，
# 避免把管理员在后台改过的名字又刷回去（PATCH /admin/roles/{id} 允许改系统角色的
# name，和 permissions_customized 保护的是同一类问题）。
LEGACY_ROLE_NAMES: dict[str, frozenset[str]] = {
    GUEST_ROLE_CODE: frozenset({"受限访客"}),
}


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
    # 调用已授权连接器与普通对话同级：能否调用由用户自己的授权决定，
    # 不是管理动作，所以 member 就能配置给自定义角色。
    "mcp.execute": 40,
    # `/` 插件面板与普通对话同级：member 也能使用，由插件自身的工具依赖继续收窄。
    # 同时它也是「应用」页面的浏览码，门槛必须压到访客等级，否则后台会出现
    # 「能勾功能却勾不了页面」的残缺分组（见 tests/test_page_catalog.py）。
    "plugin.access": 20,
    "ask.workspace.delete": 60,
    "ask.command.execute": 60,
    "agent.workspace.write": 60,
    "agent.workspace.delete": 60,
    "agent.command.execute": 60,
    # 访客（20）即可配置的基础对话
    "chat.access": 20,
    # ------------------------------------------------------------------
    # 页面浏览与功能使用：全部设为访客等级（20）
    #
    # 这两组权限的用途就是「把某些角色收窄成只读 / 受限」，而收窄的对象
    # 通常正是访客或按访客等级创建的自定义角色。若沿用默认门槛 40，
    # 管理员给 guest 勾任何一项都会 400，功能无法成立。
    # 它们都不是管理动作，放开给最低等级不会造成权限放大。
    # ------------------------------------------------------------------
    "history.access": 20,
    "assets.access": 20,
    "creation.access": 20,
    "flow.access": 20,
    "docs.access": 20,
    # admin.access 也是「页面浏览」权限（进入管理后台页面），与其它页面浏览
    # 一样对所有角色开放配置。它只决定「能不能进这个页面」：就算低等级角色
    # 被勾上，进去后管理接口仍由 user.read / role.manage 等 80 级权限把关，
    # 看不到也调不动任何数据——页面准入与页面内功能是两条独立的边界。
    "admin.access": 20,
    # plugin / mcp 的 .access 既是页面浏览码（应用 / 连接器），也是功能码。
    # 页面浏览一律按访客等级放开，理由同上：否则分组里的浏览开关被 min_rank
    # 过滤掉，只剩下一个不完整的页面。
    "mcp.access": 20,
    "chat.write": 20,
    "history.write": 20,
    "content.write": 20,
    "flow.write": 20,
    "docs.write": 20,
    "plugin.write": 20,
    "profile.write": 20,
    "recharge.write": 20,
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
