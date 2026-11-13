"""RBAC 授权服务与角色/额度/审计服务。只在本机数据库上运行。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.authorization.application.audit_service import AuditService, QuotaService
from app.authorization.application.authorization_service import AuthorizationService
from app.authorization.application.role_service import RoleService, UserPermissionService
from app.authorization.seed import seed_authorization
from app.core.config import Settings, get_settings
from app.core.database.redis import close_redis
from app.core.database.session import dispose_engine, get_session_factory, init_engine
from app.core.exceptions import PermissionDeniedError, QuotaExceededError, SuperAdminProtectedError
from app.models.rbac import Permission, ResourceShare, Role, UserRole
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


async def test_set_role_permissions_rejects_over_rank(ready_database: Settings) -> None:
    """低等级角色不能被授予超过其等级的管理权限。"""
    del ready_database
    factory = get_session_factory()
    async with factory() as session:
        service = RoleService(session)
        member_id = await _role_id("member")
        # member(40) 可配置基础权限
        await service.set_role_permissions(member_id, {"chat.access", "ask.workspace.write"})
        # member 不能被授予 user.create / role.manage 等管理权限
        with pytest.raises(Exception):
            await service.set_role_permissions(
                member_id, {"chat.access", "user.create", "role.manage"}
            )
        await session.rollback()


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
