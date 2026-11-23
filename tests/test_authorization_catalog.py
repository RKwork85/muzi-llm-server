"""权限目录与内置角色矩阵的静态校验（不依赖数据库）。"""

from __future__ import annotations

from app.authorization.domain.catalog import (
    ADMIN_ROLE_CODE,
    BUILTIN_ROLES,
    DEVELOPER_ROLE_CODE,
    GUEST_ROLE_CODE,
    MEMBER_ROLE_CODE,
    PERMISSION_CATALOG,
    ROLE_BY_CODE,
    SUPER_ADMIN_ROLE_CODE,
    TOOL_PERMISSION_RESOURCES,
    builtin_role_permissions,
    permission_min_rank,
    role_grant_rank,
)


def test_permission_codes_are_unique_and_wellformed() -> None:
    codes = [spec.code for spec in PERMISSION_CATALOG]
    assert len(codes) == len(set(codes)), "权限码重复"
    for spec in PERMISSION_CATALOG:
        if spec.mode:
            assert spec.code == f"{spec.mode}.{spec.resource}.{spec.action}"
        else:
            assert spec.code == f"{spec.resource}.{spec.action}"
        assert spec.code.count(".") >= 1


def test_builtin_roles_reference_valid_permissions() -> None:
    valid = {spec.code for spec in PERMISSION_CATALOG}
    role_codes = [role.code for role in BUILTIN_ROLES]
    assert len(role_codes) == len(set(role_codes))
    for role in BUILTIN_ROLES:
        assert role.permissions <= valid, f"{role.code} 引用了未知权限"
    assert {role.code for role in BUILTIN_ROLES} == {
        SUPER_ADMIN_ROLE_CODE,
        "admin",
        "developer",
        MEMBER_ROLE_CODE,
        "guest",
    }


def test_super_admin_has_no_explicit_permissions() -> None:
    # super_admin 隐式全部权限，不写 role_permissions，防止被误删。
    assert ROLE_BY_CODE[SUPER_ADMIN_ROLE_CODE].permissions == frozenset()


def test_member_has_basic_permissions_not_admin() -> None:
    member = ROLE_BY_CODE[MEMBER_ROLE_CODE].permissions
    assert "chat.access" in member
    assert "agent.execute" in member
    assert "skill.execute" in member
    assert "user.manage" not in member
    assert "role.manage" not in member
    # 已授权连接器（Notion/企微/飞书）的调用与普通对话同级：授权是用户自己的动作，
    # 权限位只决定"授权后能不能调"，所以 member 也有。
    assert "mcp.execute" in member
    assert "mcp.connect" not in member
    # 工具执行权限按模式拆分：member 在 ask 可读写、plan/agent 仅读，删除与执行代码不给
    assert "ask.workspace.read" in member
    assert "ask.workspace.write" in member
    assert "ask.workspace.delete" not in member
    assert "ask.command.execute" not in member
    assert "plan.workspace.read" in member
    assert "plan.workspace.write" not in member
    assert "agent.workspace.read" in member
    assert "agent.workspace.write" not in member


def test_guest_is_restricted() -> None:
    guest = ROLE_BY_CODE["guest"].permissions
    # 访客是「只读 / 受限」场景的现成载体：只有对话页与发言权，
    # 外加媒体创作画布（该板块一直对游客开放体验），以及登录账号必备的个人中心。
    # 侧边栏只保留它自己进得去的那几个入口——受限账号看到的导航也必须受限。
    assert guest == frozenset(
        {
            "chat.access",
            "chat.write",
            "flow.access",
            "me.access",
            "sidebar.show",
            "sidebar.entry.home",
            "sidebar.entry.chat",
            "sidebar.entry.flow",
            "sidebar.entry.me",
        }
    )
    # 其余页面一律不可浏览
    assert not guest & {
        "history.access",
        "assets.access",
        "creation.access",
        "docs.access",
        "plugin.access",
        "mcp.access",
        "brand.access",
        "read.access",
    }
    # 进不去的页面也不该有侧边栏入口
    assert not guest & {
        "sidebar.entry.history",
        "sidebar.entry.apps",
        "sidebar.entry.creation",
        "sidebar.entry.assets",
        "sidebar.entry.brand",
        "sidebar.entry.admin",
    }
    # 除发言外不得有任何写操作
    assert not guest & {
        "history.write",
        "content.write",
        "flow.write",
        "docs.write",
        "profile.write",
        "recharge.write",
    }


def test_page_and_write_permissions_are_grantable_to_guest() -> None:
    """页面浏览与功能使用两类权限必须能授予最低等级角色。

    这两类权限的用途就是把角色收窄成「只读 / 受限」，而收窄对象通常正是访客。
    若沿用默认门槛 40，管理员给 guest 勾任何一项都会 400，功能无法成立。
    """
    for code in (
        "history.access",
        "assets.access",
        "creation.access",
        "flow.access",
        "docs.access",
        "me.access",
        "brand.access",
        "read.access",
        "chat.write",
        "history.write",
        "content.write",
        "flow.write",
        "docs.write",
        "profile.write",
        "recharge.write",
    ):
        assert code in {spec.code for spec in PERMISSION_CATALOG}, code
        assert permission_min_rank(code) == role_grant_rank("guest"), code


def test_sidebar_permissions_are_grantable_and_distinct_from_pages() -> None:
    """侧边栏开关必须能授予最低等级角色，且不能冒充页面浏览权限。

    入口显隐是纯展示开关：不涉及数据访问，门槛应与页面浏览同级（20），否则
    「只给某个角色留两个入口」这种收窄在后台根本做不到。同时它不能以 `.access`
    结尾——那会被当成页面浏览码混进页面清单/匿名访客快照里。
    """
    from app.authorization.domain.catalog import (
        SIDEBAR_ENTRY_CODES,
        SIDEBAR_PERMISSION_CODES,
        SIDEBAR_SHOW_CODE,
        is_page_permission,
    )

    catalog = {spec.code for spec in PERMISSION_CATALOG}
    assert SIDEBAR_PERMISSION_CODES <= catalog
    assert SIDEBAR_SHOW_CODE in SIDEBAR_PERMISSION_CODES
    assert SIDEBAR_ENTRY_CODES, "入口清单不能为空"
    for code in SIDEBAR_PERMISSION_CODES:
        assert permission_min_rank(code) == role_grant_rank("guest"), code
        assert not is_page_permission(code), f"{code} 不能长得像页面浏览码"
    # 每个入口都是独立开关，否则「少露一个图标」无法表达
    assert len(set(SIDEBAR_ENTRY_CODES)) == len(SIDEBAR_ENTRY_CODES)


def test_member_and_above_see_full_sidebar_by_default() -> None:
    """升级不能改变既有行为：内置角色的侧边栏默认全开。"""
    from app.authorization.domain.catalog import SIDEBAR_ENTRY_CODES, SIDEBAR_SHOW_CODE

    for role_code in (MEMBER_ROLE_CODE, DEVELOPER_ROLE_CODE, ADMIN_ROLE_CODE):
        perms = builtin_role_permissions(role_code)
        assert SIDEBAR_SHOW_CODE in perms, role_code
        missing = [c for c in SIDEBAR_ENTRY_CODES if c not in perms]
        assert not missing, f"{role_code} 缺少侧边栏入口 {missing}"


def test_functional_codes_never_look_like_tool_permissions() -> None:
    """功能权限不能带模式前缀，否则会被工具分组当成内置工具展示。"""
    for spec in PERMISSION_CATALOG:
        if spec.mode:
            assert spec.resource in TOOL_PERMISSION_RESOURCES, spec.code
        else:
            assert spec.code == f"{spec.resource}.{spec.action}", spec.code


def test_developer_has_advanced_permissions() -> None:
    dev = ROLE_BY_CODE["developer"].permissions
    assert "mcp.execute" in dev
    assert "skill.install" in dev
    assert "agent.delete" in dev
    assert "admin.access" not in dev


def test_admin_has_management_permissions() -> None:
    admin = ROLE_BY_CODE["admin"].permissions
    assert "admin.access" in admin
    assert "user.manage" in admin
    assert "role.manage" in admin
    assert "permission.manage" in admin
    assert "audit.read" in admin
    assert "quota.manage" in admin


def test_permission_min_rank_blocks_low_roles_from_management() -> None:
    # 管理类权限只能授予 admin（80）及以上
    assert permission_min_rank("user.create") == 80
    assert permission_min_rank("user.delete") == 80
    assert permission_min_rank("user.manage") == 80
    assert permission_min_rank("role.manage") == 80
    assert permission_min_rank("permission.manage") == 80
    assert permission_min_rank("quota.manage") == 80

    # 基础权限门槛更低
    assert permission_min_rank("chat.access") == 20
    assert permission_min_rank("ask.workspace.write") == 40
    assert permission_min_rank("agent.workspace.write") == 60

    # member(40) / guest(20) 无法被授予管理权限
    assert permission_min_rank("user.manage") > role_grant_rank(MEMBER_ROLE_CODE)
    assert permission_min_rank("user.manage") > role_grant_rank("guest")

    # 自定义角色按 member 等级处理，可配置基础权限
    assert role_grant_rank("analyst") == role_grant_rank(MEMBER_ROLE_CODE)


def test_builtin_role_permissions_respect_min_rank() -> None:
    """内置角色默认许可不得包含超过自身等级的权限。"""
    for role in BUILTIN_ROLES:
        rank = role_grant_rank(role.code)
        for code in role.permissions:
            assert permission_min_rank(code) <= rank, f"{role.code} 拥有超门槛权限 {code}"


def test_builtin_role_permissions_is_the_delegation_boundary() -> None:
    """转授权边界只看「出厂清单」，自定义角色一律为空。

    这条约束决定了「被授权者能不能再往下授」：管理员后来手工加上的码不在清单里，
    持有者就只能享受、不能下放。因此清单必须是纯代码事实，不能被数据库配置影响。
    """
    assert builtin_role_permissions("admin") is ROLE_BY_CODE["admin"].permissions
    assert "prompt.manage" in builtin_role_permissions("admin")
    # 开发者没有管理权限，拿到也只可能是别人授予的，naturally 不可转授
    assert "prompt.manage" not in builtin_role_permissions("developer")
    # 自定义角色没有出厂清单
    assert builtin_role_permissions("analyst") == frozenset()
    # super_admin 隐式全部权限，清单本身为空，由授权服务特判
    assert builtin_role_permissions("super_admin") == frozenset()
