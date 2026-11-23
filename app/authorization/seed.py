"""权限字典与内置角色的幂等种子数据。

在数据库就绪后调用。仅补齐缺失项，不覆盖已有配置。
"""

from __future__ import annotations

import logging
import os
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authorization.domain.catalog import (
    BUILTIN_ROLES,
    LEGACY_ROLE_NAMES,
    LEGACY_TOOL_PERMISSION_CODES,
    MEMBER_ROLE_CODE,
    PERMISSION_CATALOG,
    SIDEBAR_PERMISSION_CODES,
    SUPER_ADMIN_ROLE_CODE,
    RoleSpec,
    sidebar_entry_page_access,
)
from app.models.rbac import Permission, Role, RolePermission, UserPermission, UserRole

logger = logging.getLogger("muzi_llm")


async def seed_permissions(session: AsyncSession) -> set[str]:
    """补齐权限字典，返回本次新建的权限码。

    返回值用于让 ``seed_role_permissions`` 区分「管理员配置过的旧码」与
    「本次上线才出现的新码」：新码不可能被任何人配置过，所以对已定制角色
    也应当补上默认值，否则新增功能对它们永远不可用。
    """
    existing = {
        code
        for code in await session.scalars(select(Permission.code))
    }
    created: set[str] = set()
    for spec in PERMISSION_CATALOG:
        if spec.code in existing:
            continue
        created.add(spec.code)
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
    await _drop_legacy_tool_permissions(session)
    return created


async def _drop_legacy_tool_permissions(session: AsyncSession) -> None:
    """删除无模式前缀的旧工具执行权限码（已拆分为 mode.resource.action）。"""
    legacy_ids = list(
        await session.scalars(
            select(Permission.id).where(Permission.code.in_(LEGACY_TOOL_PERMISSION_CODES))
        )
    )
    if not legacy_ids:
        return
    await session.execute(
        delete(RolePermission).where(RolePermission.permission_id.in_(legacy_ids))
    )
    await session.execute(
        delete(UserPermission).where(UserPermission.permission_id.in_(legacy_ids))
    )
    await session.execute(
        delete(Permission).where(Permission.id.in_(legacy_ids))
    )
    await session.flush()
    logger.info("dropped legacy tool permissions: %s", sorted(LEGACY_TOOL_PERMISSION_CODES))


def should_sync_builtin_text(role: Role, spec: RoleSpec) -> bool:
    """内置角色的 name/description 是否该被代码里的「出厂文案」覆盖。

    只在两种情况下同步：名字为空，或名字仍是旧默认值（说明没人改过）。
    管理员可以通过 PATCH /admin/roles/{id} 改系统角色的名字，那种改名必须保住。
    """
    if not role.is_system:
        return False
    if not role.name:
        return True
    return role.name in LEGACY_ROLE_NAMES.get(spec.code, frozenset())


async def _drop_retired_builtin_roles(session: AsyncSession) -> None:
    """删除已从代码里移除的内置角色（连同其权限与用户关联）。

    builtin 角色由 BUILTIN_ROLES 定义，代码是唯一事实来源。某个内置角色从
    代码里移除后，数据库残留的行会让后台继续显示它，而它因为 is_system=True
    无法走 delete_role 删除，只能在这里按代码事实清理。
    """
    current_codes = {spec.code for spec in BUILTIN_ROLES}
    retired_ids = list(
        await session.scalars(
            select(Role.id).where(Role.is_system.is_(True), ~Role.code.in_(current_codes))
        )
    )
    if not retired_ids:
        return
    await session.execute(delete(RolePermission).where(RolePermission.role_id.in_(retired_ids)))
    await session.execute(delete(UserRole).where(UserRole.role_id.in_(retired_ids)))
    await session.execute(delete(Role).where(Role.id.in_(retired_ids)))
    await session.flush()
    logger.info("dropped retired builtin roles: %s", sorted(retired_ids))


async def seed_roles(session: AsyncSession) -> None:
    """新建缺失的内置角色，并把改过名的内置角色同步到最新文案。

    name / description 属于「出厂文案」：代码是唯一事实来源。但管理员可以在后台
    改系统角色的名字，所以不能无条件覆盖——只在该角色当前名字仍是旧默认值时改写，
    已经人工改过的保持原样。同时清理已从代码移除的内置角色。
    """
    await _drop_retired_builtin_roles(session)
    existing = {
        role.code: role for role in await session.scalars(select(Role))
    }
    for spec in BUILTIN_ROLES:
        role = existing.get(spec.code)
        if role is None:
            session.add(
                Role(
                    code=spec.code,
                    name=spec.name,
                    description=spec.description,
                    is_system=True,
                    status="active",
                )
            )
            continue
        if not should_sync_builtin_text(role, spec):
            continue
        if role.name != spec.name:
            logger.info("sync builtin role name %s: %s -> %s", spec.code, role.name, spec.name)
        role.name = spec.name
        role.description = spec.description
    await session.flush()


async def seed_role_permissions(session: AsyncSession, newly_created: set[str] | None = None) -> None:
    """为内置角色补齐权限关联。

    - super_admin 跳过（隐式全部权限）。
    - 被管理员在后台改过的角色（permissions_customized=true）只补「本次新建」的
      权限码，其余保持管理员的配置。否则每次启动都会把管理员取消掉的勾选加回来，
      表现为「权限设置自己会变回去」；但完全不补又会让新功能对这些角色永远锁死。
    """
    newly_created = newly_created or set()
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
    customized = {
        code
        for code, flag in (
            await session.execute(select(Role.code, Role.permissions_customized))
        ).all()
        if flag
    }
    if customized:
        logger.info("skip seeding customized roles: %s", sorted(customized))
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
        # 已定制角色只接受本次新建的码，保留管理员已有的勾选状态。
        allow_all = spec.code not in customized
        for perm_code in spec.permissions:
            if not allow_all and perm_code not in newly_created:
                continue
            perm_id = perms.get(perm_code)
            if perm_id is None:
                continue
            if (role_id, perm_id) not in existing_pairs:
                session.add(RolePermission(role_id=role_id, permission_id=perm_id))
                existing_pairs.add((role_id, perm_id))
    # 侧边栏入口权限是纯展示开关，而且只对「升级前就已经存在的角色」有破坏性：
    # 上面只覆盖内置角色，自定义角色拿不到新码，侧边栏会在升级那一刻整块消失。
    # 那不是管理员做的决定，而是升级的副作用，所以新出现的侧边栏码单独补发。
    await grant_sidebar_permissions(session, newly_created)
    await session.flush()


async def grant_sidebar_permissions(session: AsyncSession, created: set[str] | None = None) -> int:
    """把「本次新增」的侧边栏码补发给所有已有角色，返回新增关联条数。

    为什么只有这一组权限需要跨角色补发：其余权限都是功能/数据授权，新功能对
    自定义角色默认关闭是安全的（管理员按需开放即可）；而侧边栏码是展示开关，
    缺了它升级当天所有人的导航会凭空少几项——那是升级造成的回退，不是配置选择。

    补发是有选择性的：某个入口对应的页面这个角色本来就进不去，就不补那个入口，
    免得后台出现「勾着管理后台入口、却没有 admin.access」这种自相矛盾的配置。
    跳过超管（隐式全权）；匿名访客也不在这里补——游客侧边栏由后台对 anonymous
    角色自己的勾选决定（见 /public/anonymous-access），自动补发会把管理员
    刚关掉的入口又加回来。幂等。
    """
    codes = {code for code in (created or set()) if code in SIDEBAR_PERMISSION_CODES}
    if not codes:
        return 0
    perm_ids = {
        code: perm_id
        for code, perm_id in (
            await session.execute(
                select(Permission.code, Permission.id).where(Permission.code.in_(codes))
            )
        ).all()
    }
    role_rows = (await session.execute(select(Role.code, Role.id))).all()
    existing_pairs = {
        (role_id, perm_id)
        for role_id, perm_id in (
            await session.execute(
                select(RolePermission.role_id, RolePermission.permission_id)
            )
        ).all()
    }
    role_perms: dict[object, set[str]] = {}
    for role_id, perm_code in (
        await session.execute(
            select(RolePermission.role_id, Permission.code).join(
                Permission, Permission.id == RolePermission.permission_id
            )
        )
    ).all():
        role_perms.setdefault(role_id, set()).add(perm_code)
    skip = {SUPER_ADMIN_ROLE_CODE, "anonymous"}
    added = 0
    for role_code, role_id in role_rows:
        if role_code in skip:
            continue
        owned = role_perms.get(role_id, set())
        for code in codes:
            perm_id = perm_ids.get(code)
            if perm_id is None or (role_id, perm_id) in existing_pairs:
                continue
            # 入口没有意义时（页面进不去）不补：入口显隐不能凌驾于页面准入之上
            page_access = sidebar_entry_page_access(code)
            if page_access is not None and page_access not in owned:
                continue
            session.add(RolePermission(role_id=role_id, permission_id=perm_id))
            existing_pairs.add((role_id, perm_id))
            added += 1
    return added


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
    created = await seed_permissions(session)
    await seed_roles(session)
    await seed_role_permissions(session, created)
    await seed_default_member_role(session)
    await seed_bootstrap_super_admin(session)
    # 官方工作流案例的内置数据：与权限 seed 一起补齐，保持启动就绪。
    try:
        from app.services.flow_official_seed import seed_official_workflows

        await seed_official_workflows(session)
    except Exception:  # 表尚未迁移等情况不阻断授权 seed
        logger.exception("official workflows seed failed")
    logger.info("authorization seed completed")
