"""权限字典与内置角色的幂等种子数据。

在数据库就绪后调用。仅补齐缺失项，不覆盖已有配置。
"""

from __future__ import annotations

import logging
import os
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authorization.domain.catalog import (
    BUILTIN_ROLES,
    MEMBER_ROLE_CODE,
    PERMISSION_CATALOG,
    SUPER_ADMIN_ROLE_CODE,
)
from app.models.rbac import Permission, Role, RolePermission, UserRole

logger = logging.getLogger("muzi_llm")


async def seed_permissions(session: AsyncSession) -> None:
    existing = {
        code
        for code in await session.scalars(select(Permission.code))
    }
    for spec in PERMISSION_CATALOG:
        if spec.code in existing:
            continue
        session.add(
            Permission(
                code=spec.code,
                name=spec.name,
                resource=spec.resource,
                action=spec.action,
                description=spec.description,
                is_system=True,
            )
        )
    await session.flush()


async def seed_roles(session: AsyncSession) -> None:
    existing_codes = {code for code in await session.scalars(select(Role.code))}
    for spec in BUILTIN_ROLES:
        if spec.code in existing_codes:
            continue
        session.add(
            Role(
                code=spec.code,
                name=spec.name,
                description=spec.description,
                is_system=True,
                status="active",
            )
        )
    await session.flush()


async def seed_role_permissions(session: AsyncSession) -> None:
    """为内置角色补齐权限关联。super_admin 跳过（隐式全部权限）。"""
    perms = {
        code: row
        for code, row in (
            await session.execute(select(Permission.code, Permission.id))
        ).all()
    }
    roles = {
        code: row
        for code, row in (await session.execute(select(Role.code, Role.id))).all()
    }
    # 已存在的 (role_id, permission_id) 关联
    existing_pairs = {
        (role_id, perm_id)
        for role_id, perm_id in (
            await session.execute(select(RolePermission.role_id, RolePermission.permission_id))
        ).all()
    }
    for spec in BUILTIN_ROLES:
        role_id = roles.get(spec.code)
        if role_id is None:
            continue
        for perm_code in spec.permissions:
            perm_id = perms.get(perm_code)
            if perm_id is None:
                continue
            if (role_id, perm_id) not in existing_pairs:
                session.add(RolePermission(role_id=role_id, permission_id=perm_id))
                existing_pairs.add((role_id, perm_id))
    await session.flush()


async def seed_bootstrap_super_admin(session: AsyncSession) -> None:
    """确保至少存在一个超级管理员。优先使用 MUZI_ACCOUNT_USER_ID，否则取最早注册的用户。"""
    count = await session.scalar(
        select(UserRole.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(Role.code == SUPER_ADMIN_ROLE_CODE, Role.status == "active")
        .limit(1)
    )
    if count is not None:
        return
    from app.models.tables import User

    user_id: uuid.UUID | None = None
    configured = os.getenv("MUZI_ACCOUNT_USER_ID", "").strip()
    if configured:
        try:
            candidate = uuid.UUID(configured)
            if await session.get(User, candidate) is not None:
                user_id = candidate
        except ValueError:
            user_id = None
    if user_id is None:
        user_id = await session.scalar(select(User.id).order_by(User.created_at.asc()).limit(1))
    if user_id is None:
        return
    role = await session.scalar(select(Role).where(Role.code == SUPER_ADMIN_ROLE_CODE))
    if role is None:
        return
    session.add(UserRole(user_id=user_id, role_id=role.id))
    logger.info("bootstrap super admin assigned to %s", user_id)


async def seed_default_member_role(session: AsyncSession) -> None:
    """为没有任何角色的用户赋予默认 member 角色，保证存量用户不被默认拒绝。"""
    from app.models.tables import User

    member = await session.scalar(select(Role).where(Role.code == MEMBER_ROLE_CODE))
    if member is None:
        return
    user_ids = list(await session.scalars(select(User.id)))
    assigned = {
        user_id
        for user_id in await session.scalars(select(UserRole.user_id).distinct())
    }
    for user_id in user_ids:
        if user_id not in assigned:
            session.add(UserRole(user_id=user_id, role_id=member.id))


async def seed_authorization(session: AsyncSession) -> None:
    await seed_permissions(session)
    await seed_roles(session)
    await seed_role_permissions(session)
    await seed_default_member_role(session)
    await seed_bootstrap_super_admin(session)
    logger.info("authorization seed completed")
