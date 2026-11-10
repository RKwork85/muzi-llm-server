"""权限目录与内置角色矩阵的静态校验（不依赖数据库）。"""

from __future__ import annotations

from app.authorization.domain.catalog import (
    BUILTIN_ROLES,
    MEMBER_ROLE_CODE,
    PERMISSION_CATALOG,
    ROLE_BY_CODE,
    SUPER_ADMIN_ROLE_CODE,
)


def test_permission_codes_are_unique_and_wellformed() -> None:
    codes = [spec.code for spec in PERMISSION_CATALOG]
    assert len(codes) == len(set(codes)), "权限码重复"
    for spec in PERMISSION_CATALOG:
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
