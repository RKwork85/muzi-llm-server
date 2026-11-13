"""权限目录与内置角色矩阵的静态校验（不依赖数据库）。"""

from __future__ import annotations

from app.authorization.domain.catalog import (
    BUILTIN_ROLES,
    MEMBER_ROLE_CODE,
    PERMISSION_CATALOG,
    ROLE_BY_CODE,
    SUPER_ADMIN_ROLE_CODE,
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
    assert "mcp.execute" not in member
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
    assert ROLE_BY_CODE["guest"].permissions == frozenset({"chat.access"})


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
