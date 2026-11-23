"""RBAC 授权服务与角色/额度/审计服务。只在本机数据库上运行。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from app.authorization.application.audit_service import AuditService, QuotaService
from app.authorization.application.authorization_service import AuthorizationService
from app.authorization.application.role_service import RoleService, UserPermissionService
from app.authorization.seed import seed_authorization
from app.core.config import Settings, get_settings
from app.core.database.redis import close_redis
from app.core.database.session import dispose_engine, get_session_factory, init_engine
from app.core.exceptions import (
    HierarchyDeniedError,
    PermissionDeniedError,
    QuotaExceededError,
    SuperAdminProtectedError,
)
from app.models.rbac import Permission, ResourceShare, Role, RolePermission, UserRole
from app.models.tables import User
from tests.database.integration.test_postgres import _identity


@pytest.fixture
async def ready_database():
    get_settings.cache_clear()
    loaded = Settings()
    init_engine(loaded)
    try:
        yield loaded
    finally:
        await close_redis()
        await dispose_engine()


async def _remove_user_full(user_id: uuid.UUID) -> None:
    from sqlalchemy import text

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text("DELETE FROM access_audit_logs WHERE actor_id = :id OR subject_id = :id"),
            {"id": user_id},
        )
        await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        await session.commit()


async def _create_user() -> User:
    email, username = _identity()
    factory = get_session_factory()
    async with factory() as session:
        user = User(email=email, username=username, status="active")
        session.add(user)
        await session.commit()
        return user


async def _role_id(code: str) -> uuid.UUID:
    factory = get_session_factory()
    async with factory() as session:
        role = await session.scalar(select(Role).where(Role.code == code))
        assert role is not None
        return role.id


async def test_seed_is_idempotent(ready_database: Settings) -> None:
    del ready_database
    factory = get_session_factory()
    async with factory() as session:
        await seed_authorization(session)
        await session.commit()
        perm_count = len(list(await session.scalars(select(Permission.id))))
        role_count = len(list(await session.scalars(select(Role.id))))
    async with factory() as session:
        await seed_authorization(session)
        await session.commit()
        assert len(list(await session.scalars(select(Permission.id)))) == perm_count
        assert len(list(await session.scalars(select(Role.id)))) == role_count


async def test_member_has_basic_not_admin(ready_database: Settings) -> None:
    del ready_database
    user = await _create_user()
    try:
        member_id = await _role_id("member")
        factory = get_session_factory()
        async with factory() as session:
            await RoleService(session).assign_role_to_user(user.id, member_id)
            await session.commit()
        async with factory() as session:
            authz = AuthorizationService(session)
            assert await authz.has_permission(user.id, "chat.access") is True
            assert await authz.has_permission(user.id, "agent.execute") is True
            assert await authz.has_permission(user.id, "user.manage") is False
            assert await authz.has_permission(user.id, "role.manage") is False
    finally:
        await _remove_user_full(user.id)


async def test_deny_priority_overrides_role(ready_database: Settings) -> None:
    del ready_database
    user = await _create_user()
    try:
        member_id = await _role_id("member")
        factory = get_session_factory()
        async with factory() as session:
            service = RoleService(session)
            await service.assign_role_to_user(user.id, member_id)
            await UserPermissionService(session).set_user_permission(user.id, "chat.access", "deny")
            await session.commit()
        async with factory() as session:
            authz = AuthorizationService(session)
            assert await authz.has_permission(user.id, "chat.access") is False
            assert await authz.has_permission(user.id, "agent.execute") is True
    finally:
        await _remove_user_full(user.id)


async def test_super_admin_bypasses_and_unknown_denied(ready_database: Settings) -> None:
    del ready_database
    user = await _create_user()
    try:
        super_id = await _role_id("super_admin")
        factory = get_session_factory()
        async with factory() as session:
            await RoleService(session).assign_role_to_user(user.id, super_id)
            await session.commit()
        async with factory() as session:
            authz = AuthorizationService(session)
            assert await authz.has_permission(user.id, "user.manage") is True
            assert await authz.has_permission(user.id, "quota.manage") is True
            snapshot = await authz.snapshot(user.id)
            assert snapshot.data_scope == "all"
    finally:
        await _remove_user_full(user.id)


async def test_require_permission_raises(ready_database: Settings) -> None:
    del ready_database
    user = await _create_user()
    try:
        member_id = await _role_id("member")
        factory = get_session_factory()
        async with factory() as session:
            await RoleService(session).assign_role_to_user(user.id, member_id)
            await session.commit()
        async with factory() as session:
            with pytest.raises(PermissionDeniedError):
                await AuthorizationService(session).require_permission(user.id, "role.manage")
    finally:
        await _remove_user_full(user.id)


async def test_resource_access_owner_and_share(ready_database: Settings) -> None:
    del ready_database
    owner = await _create_user()
    grantee = await _create_user()
    try:
        member_id = await _role_id("member")
        factory = get_session_factory()
        async with factory() as session:
            service = RoleService(session)
            await service.assign_role_to_user(owner.id, member_id)
            await service.assign_role_to_user(grantee.id, member_id)
            await session.commit()
        async with factory() as session:
            authz = AuthorizationService(session)
            assert await authz.has_resource_access(
                owner.id, "agent.execute", "agent", "ag-1", owner_id=owner.id
            ) is True
            assert await authz.has_resource_access(
                grantee.id, "agent.execute", "agent", "ag-1", owner_id=owner.id
            ) is False
        async with factory() as session:
            session.add(
                ResourceShare(
                    resource_type="agent",
                    resource_id="ag-1",
                    owner_id=owner.id,
                    grantee_id=grantee.id,
                    permission="agent.execute",
                )
            )
            await session.commit()
        async with factory() as session:
            authz = AuthorizationService(session)
            assert await authz.has_resource_access(
                grantee.id, "agent.execute", "agent", "ag-1", owner_id=owner.id
            ) is True
    finally:
        await _remove_user_full(owner.id)
        await _remove_user_full(grantee.id)


async def test_system_role_and_super_admin_protected(ready_database: Settings) -> None:
    del ready_database
    factory = get_session_factory()
    async with factory() as session:
        service = RoleService(session)
        super_id = await _role_id("super_admin")
        member_id = await _role_id("member")
        with pytest.raises(SuperAdminProtectedError):
            await service.delete_role(super_id)
        with pytest.raises(SuperAdminProtectedError):
            await service.delete_role(member_id)
        with pytest.raises(SuperAdminProtectedError):
            await service.update_role(super_id, status="disabled")
        with pytest.raises(SuperAdminProtectedError):
            await service.set_role_permissions(super_id, {"chat.access"})
        await session.rollback()


async def test_set_role_permissions_limits_delegation(ready_database: Settings) -> None:
    """转授权边界：只能分配自己可转授的权限，且不能改等级更高的角色。"""
    del ready_database
    factory = get_session_factory()
    async with factory() as session:
        service = RoleService(session)
        member_id = await _role_id("member")
        admin_id = await _role_id("admin")
        # 可转授集合之外的码一律拒绝（被授权者不能再下放）
        with pytest.raises(PermissionDeniedError):
            await service.set_role_permissions(
                member_id,
                {"chat.access", "prompt.manage"},
                grantable=frozenset({"chat.access"}),
                actor_rank=80,
            )
        # 已存在的项允许原样保留，避免下级管理员保存一次就删掉上级授予的权限
        await service.set_role_permissions(
            member_id,
            {"chat.access", "prompt.manage"},
            grantable=frozenset({"chat.access", "prompt.manage"}),
            actor_rank=80,
        )
        await service.set_role_permissions(
            member_id,
            {"chat.access", "prompt.manage"},
            grantable=frozenset({"chat.access"}),
            actor_rank=80,
        )
        # 不能改动等级高于自己的角色
        with pytest.raises(HierarchyDeniedError):
            await service.set_role_permissions(
                admin_id, {"chat.access"}, grantable=frozenset({"chat.access"}), actor_rank=60
            )
        await session.rollback()


async def test_every_page_access_code_is_written_to_database(ready_database: Settings) -> None:
    """页面浏览码必须真的写进 permissions 表。

    后台角色面板的勾选、前端侧边栏的显隐都由数据库授权驱动：清单里写了码，
    但 seed 没把它落到 permissions 表，管理员就勾不到、也存不进去——表现为
    「界面上有这个分组，保存后不生效」。所以这里逐条核对入库。
    """
    from app.authorization.domain.pages import PAGE_CATALOG

    del ready_database
    factory = get_session_factory()
    async with factory() as session:
        await seed_authorization(session)
        await session.commit()
    async with factory() as session:
        stored = set(await session.scalars(select(Permission.code)))
    expected = {spec.access for spec in PAGE_CATALOG if spec.access}
    assert expected, "页面清单不允许为空"
    assert not expected - stored, f"以下页面浏览码未入库：{sorted(expected - stored)}"


async def test_seed_backfills_new_sidebar_permissions_for_existing_roles(
    ready_database: Settings,
) -> None:
    """升级新增的侧边栏码必须补发给「升级前就存在」的角色。

    内置角色由 BUILTIN_ROLES 覆盖，自定义角色不在其中：如果不补发，升级当天
    它们的侧边栏会整块消失——那是升级副作用，不是管理员的选择。
    但补发不能越过页面准入：进不去的页面不该拿到入口。
    """
    from app.authorization.seed import grant_sidebar_permissions
    from app.models.rbac import Role as RoleModel

    del ready_database
    suffix = uuid.uuid4().hex[:8]
    with_page = f"legacy_haspage_{suffix}"
    without_page = f"legacy_nopage_{suffix}"
    factory = get_session_factory()
    async with factory() as session:
        await seed_authorization(session)
        session.add(RoleModel(code=with_page, name="有页面访问的旧角色", is_system=False))
        session.add(RoleModel(code=without_page, name="无页面访问的旧角色", is_system=False))
        await session.commit()
    async with factory() as session:
        chat_perm = await session.scalar(select(Permission).where(Permission.code == "chat.access"))
        role = await session.scalar(select(Role).where(Role.code == with_page))
        session.add(RolePermission(role_id=role.id, permission_id=chat_perm.id))
        await session.commit()

    async with factory() as session:
        # 模拟「本次启动才出现」的侧边栏码：即使库里已存在，函数也只会补缺失关联。
        await grant_sidebar_permissions(session, {"sidebar.show", "sidebar.entry.home"})
        await session.commit()

    async with factory() as session:
        rows = (
            await session.execute(
                select(Role.code, Permission.code)
                .join(RolePermission, RolePermission.role_id == Role.id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(Permission.code.in_({"sidebar.show", "sidebar.entry.home"}))
            )
        ).all()
        by_role: dict[str, set[str]] = {}
        for role_code, perm_code in rows:
            by_role.setdefault(role_code, set()).add(perm_code)
        # 有 chat.access 的角色：总开关 + 首页入口一起补上
        assert by_role.get(with_page) == {"sidebar.show", "sidebar.entry.home"}
        # 进不去对话页的角色：只补总开关，不补它用不到的入口
        assert by_role.get(without_page) == {"sidebar.show"}
        # 超管隐式全权、匿名访客没有侧边栏：都不写关联
        assert "super_admin" not in by_role
        assert "anonymous" not in by_role
        # 幂等：再跑一次不新增
        before = sum(len(v) for v in by_role.values())
        assert await grant_sidebar_permissions(session, {"sidebar.show", "sidebar.entry.home"}) == 0
        await session.rollback()
        assert before > 0

    # 收尾：这两个角色是测试造出来的，不能留在开发库里
    async with factory() as session:
        ids = list(
            await session.scalars(
                select(Role.id).where(Role.code.in_({with_page, without_page}))
            )
        )
        if ids:
            await session.execute(
                delete(RolePermission).where(RolePermission.role_id.in_(ids))
            )
            await session.execute(delete(Role).where(Role.id.in_(ids)))
            await session.commit()


async def test_quota_consume_and_audit(ready_database: Settings) -> None:
    del ready_database
    user = await _create_user()
    try:
        factory = get_session_factory()
        async with factory() as session:
            quota_service = QuotaService(session)
            await quota_service.set_quota(user.id, "total_tokens", 5, limit_source="manual")
            first = await quota_service.settle(
                user.id, source="test", settlement_key="a", amount=3, kind="chat"
            )
            second = await quota_service.settle(
                user.id, source="test", settlement_key="b", amount=2, kind="chat"
            )
            assert first.status == "settled"
            assert second.status == "settled"
            with pytest.raises(QuotaExceededError):
                await quota_service.authorize(user.id)
            third = await quota_service.settle(
                user.id, source="test", settlement_key="c", amount=1, kind="chat"
            )
            assert third.status == "exceeded"
            assert third.used_value == 6
            again = await quota_service.settle(
                user.id, source="test", settlement_key="c", amount=1, kind="chat"
            )
            assert again.status == "duplicate"
            assert again.used_value == 6
            await session.rollback()
        async with factory() as session:
            await AuditService(session).record(
                user.id, "user_status_change", subject_id=user.id, context={"before": "active", "after": "disabled"}
            )
            await session.commit()
            logs = await AuditService(session).list_logs(subject_id=user.id, limit=10)
            assert any(log.action == "user_status_change" for log in logs)
    finally:
        await _remove_user_full(user.id)
