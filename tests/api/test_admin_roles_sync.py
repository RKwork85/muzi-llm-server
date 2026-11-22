"""角色管理页的后端契约：角色输出字段、内置角色同步端点。

只测纯函数与路由表，不连数据库——同步本身的幂等性由 seed 的用例覆盖。
"""

from __future__ import annotations

import uuid

from app.api.v1.endpoints.admin import router as admin_router
from app.models.rbac import Role
from app.schemas.rbac import RoleOut


def _role_out(code: str) -> RoleOut:
    """rank 是 RoleOut 按 code 推导的，所以这里不传。"""
    return RoleOut.model_validate(
        Role(
            id=uuid.uuid4(),
            code=code,
            name=code,
            is_system=True,
            status="active",
        )
    )


def test_role_rank_is_derived_from_code() -> None:
    """rank 来自代码目录的 role_grant_rank，不由数据库决定。"""
    assert _role_out("member").rank == 40
    assert _role_out("guest").rank == 20


def test_sync_builtin_endpoint_is_registered() -> None:
    """同步端点是后台自愈的入口，路由写错就等于这个功能不存在。"""
    paths = {
        (route.path, method)
        for route in admin_router.routes
        for method in getattr(route, "methods", set())
    }
    assert ("/admin/roles/sync-builtin", "POST") in paths
