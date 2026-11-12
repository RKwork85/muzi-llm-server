"""管理后台：用户、角色、权限、审计、额度。

所有端点都由后端校验权限，前端隐藏菜单不作为安全措施。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.authorization.application.audit_service import AuditService, QuotaService
from app.authorization.application.authorization_service import AuthorizationService
from app.authorization.application.role_service import RoleService, UserPermissionService
from app.authorization.dependencies import require_permission
from app.authorization.domain.catalog import max_role_rank
from app.core.database.deps import DbSession
from app.core.exceptions import HierarchyDeniedError, UserNotFoundError
from app.models.rbac import Permission, Role, UserRole
from app.models.tables import User
from app.schemas.rbac import (
    AuditLogOut,
    PermissionOut,
    QuotaAdjust,
    QuotaOut,
    QuotaSet,
    RoleCreate,
    RoleOut,
    RolePermissionSet,
    RoleUpdate,
    UserAdminOut,
    UserPasswordReset,
    UserPermissionAssign,
    UserRoleAssign,
    UserStatusUpdate,
)
from app.services.auth_service import AuthService

router = APIRouter(prefix="/admin", tags=["admin"])


async def _user_admin_out(
    session: DbSession,
    user: User,
    authz: AuthorizationService,
) -> UserAdminOut:
    snapshot = await authz.snapshot(user.id)
    return UserAdminOut(
        id=user.id,
        email=user.email,
        username=user.username,
        display_name=user.display_name,
        status=user.status,
        avatar=user.avatar,
        phone=user.phone,
        roles=list(snapshot.roles),
        permissions=sorted(snapshot.permissions),
        data_scope=snapshot.data_scope,
    )


# ---------------------------------------------------------------------------
# 用户管理
# ---------------------------------------------------------------------------

@router.get("/users", response_model=list[UserAdminOut], summary="列出用户")
async def list_users(
    session: DbSession,
    _: Annotated[User, Depends(require_permission("user.read"))],
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[UserAdminOut]:
    authz = AuthorizationService(session)
    users = list(
        await session.scalars(
            select(User).order_by(User.created_at.desc()).limit(limit).offset(offset)
        )
    )
    return [await _user_admin_out(session, user, authz) for user in users]


@router.get("/users/{user_id}", response_model=UserAdminOut, summary="查看用户详情与有效权限")
async def get_user(
    user_id: uuid.UUID,
    session: DbSession,
    _: Annotated[User, Depends(require_permission("user.read"))],
) -> UserAdminOut:
    user = await session.get(User, user_id)
    if user is None:
        raise UserNotFoundError()
    return await _user_admin_out(session, user, AuthorizationService(session))


@router.patch("/users/{user_id}/status", response_model=UserAdminOut, summary="封禁或解封用户")
async def update_user_status(
    user_id: uuid.UUID,
    body: UserStatusUpdate,
    session: DbSession,
    actor: Annotated[User, Depends(require_permission("user.manage"))],
) -> UserAdminOut:
    user = await session.get(User, user_id)
    if user is None:
        raise UserNotFoundError()
    if body.status == "disabled":
        await _guard_last_super_admin(session, user)
    before = user.status
    user.status = body.status
    await AuditService(session).record(
        actor.id,
        "user_status_change",
        subject_id=user.id,
        outcome="success",
        context={"before": before, "after": body.status},
    )
    await session.flush()
    return await _user_admin_out(session, user, AuthorizationService(session))


@router.put("/users/{user_id}/roles", response_model=UserAdminOut, summary="设置用户角色")
async def set_user_roles(
    user_id: uuid.UUID,
    body: UserRoleAssign,
    session: DbSession,
    actor: Annotated[User, Depends(require_permission("user.manage"))],
) -> UserAdminOut:
    user = await session.get(User, user_id)
    if user is None:
        raise UserNotFoundError()
    roles = list(await session.scalars(select(Role).where(Role.id.in_(body.role_ids))))
    if len(roles) != len(set(body.role_ids)):
        from app.core.exceptions import InvalidRequestError

        raise InvalidRequestError("存在无效角色")
    await _guard_role_change(session, user, {r.code for r in roles})
    service = RoleService(session)
    existing = list(
        await session.scalars(select(UserRole).where(UserRole.user_id == user_id))
    )
    for row in existing:
        await session.delete(row)
    for role in roles:
        await service.assign_role_to_user(user_id, role.id)
    # 角色变更后同步额度：super_admin 无限；其余角色若仍是默认值则跟随新角色额度。
    await QuotaService(session).sync_quota_to_role(user_id)
    await AuditService(session).record(
        actor.id,
        "role_change",
        subject_id=user.id,
        context={"after": [r.code for r in roles]},
    )
    await session.flush()
    return await _user_admin_out(session, user, AuthorizationService(session))


@router.post("/users/{user_id}/reset-password", status_code=204, summary="重置用户密码")
async def reset_user_password(
    user_id: uuid.UUID,
    body: UserPasswordReset,
    session: DbSession,
    actor: Annotated[User, Depends(require_permission("user.manage"))],
) -> None:
    user = await session.get(User, user_id)
    if user is None:
        raise UserNotFoundError()
    await AuthService(session).reset_password(user.id, body.new_password)
    await AuditService(session).record(
        actor.id,
        "user_password_reset",
        subject_id=user.id,
        outcome="success",
    )
    await session.flush()


@router.put("/users/{user_id}/permissions", response_model=UserAdminOut, summary="用户直接授权或拒绝")
async def set_user_permissions(
    user_id: uuid.UUID,
    body: UserPermissionAssign,
    session: DbSession,
    actor: Annotated[User, Depends(require_permission("permission.manage"))],
) -> UserAdminOut:
    user = await session.get(User, user_id)
    if user is None:
        raise UserNotFoundError()
    service = UserPermissionService(session)
    for grant in body.grants:
        await service.set_user_permission(user_id, grant.code, grant.grant_type)
    await AuditService(session).record(
        actor.id,
        "permission_grant",
        subject_id=user.id,
        context={g.code: g.grant_type for g in body.grants},
    )
    await session.flush()
    return await _user_admin_out(session, user, AuthorizationService(session))


# ---------------------------------------------------------------------------
# 角色管理
# ---------------------------------------------------------------------------

@router.get("/roles", response_model=list[RoleOut], summary="列出角色")
async def list_roles(
    session: DbSession,
    _: Annotated[User, Depends(require_permission("role.read"))],
) -> list[RoleOut]:
    roles = await RoleService(session).list_roles()
    return [RoleOut.model_validate(role) for role in roles]


@router.post("/roles", response_model=RoleOut, status_code=201, summary="创建角色")
async def create_role(
    body: RoleCreate,
    session: DbSession,
    actor: Annotated[User, Depends(require_permission("role.manage"))],
) -> RoleOut:
    role = await RoleService(session).create_role(body.code, body.name, body.description)
    await AuditService(session).record(actor.id, "role_create", context={"code": role.code})
    await session.flush()
    return RoleOut.model_validate(role)


@router.patch("/roles/{role_id}", response_model=RoleOut, summary="修改角色")
async def update_role(
    role_id: uuid.UUID,
    body: RoleUpdate,
    session: DbSession,
    actor: Annotated[User, Depends(require_permission("role.manage"))],
) -> RoleOut:
    role = await RoleService(session).update_role(
        role_id,
        name=body.name,
        description=body.description,
        status=body.status,
    )
    await AuditService(session).record(actor.id, "role_update", context={"code": role.code})
    await session.flush()
    return RoleOut.model_validate(role)


@router.delete("/roles/{role_id}", status_code=204, summary="删除角色")
async def delete_role(
    role_id: uuid.UUID,
    session: DbSession,
    actor: Annotated[User, Depends(require_permission("role.manage"))],
) -> None:
    role = await RoleService(session).get_role(role_id)
    await RoleService(session).delete_role(role_id)
    await AuditService(session).record(actor.id, "role_delete", context={"code": role.code})
    await session.flush()


@router.get("/roles/{role_id}/permissions", response_model=list[str], summary="查看角色权限")
async def get_role_permissions(
    role_id: uuid.UUID,
    session: DbSession,
    _: Annotated[User, Depends(require_permission("role.read"))],
) -> list[str]:
    return sorted(await RoleService(session).get_role_permissions(role_id))


@router.put("/roles/{role_id}/permissions", response_model=list[str], summary="配置角色权限")
async def set_role_permissions(
    role_id: uuid.UUID,
    body: RolePermissionSet,
    session: DbSession,
    actor: Annotated[User, Depends(require_permission("role.manage"))],
) -> list[str]:
    codes = set(body.permission_codes)
    await RoleService(session).set_role_permissions(role_id, codes)
    await AuditService(session).record(
        actor.id,
        "role_permission_change",
        resource_id=str(role_id),
        context={"after": sorted(codes)},
    )
    await session.flush()
    return sorted(codes)


# ---------------------------------------------------------------------------
# 权限字典
# ---------------------------------------------------------------------------

@router.get("/permissions", response_model=list[PermissionOut], summary="列出权限字典")
async def list_permissions(
    session: DbSession,
    _: Annotated[User, Depends(require_permission("permission.read"))],
) -> list[PermissionOut]:
    perms = list(
        await session.scalars(select(Permission).order_by(Permission.resource, Permission.action))
    )
    return [PermissionOut.model_validate(p) for p in perms]


# ---------------------------------------------------------------------------
# 审计
# ---------------------------------------------------------------------------

@router.get("/audit", response_model=list[AuditLogOut], summary="查看审计日志")
async def list_audit(
    session: DbSession,
    _: Annotated[User, Depends(require_permission("audit.read"))],
    subject_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[AuditLogOut]:
    logs = await AuditService(session).list_logs(subject_id=subject_id, limit=limit, offset=offset)
    return [AuditLogOut.model_validate(log) for log in logs]


# ---------------------------------------------------------------------------
# 额度
# ---------------------------------------------------------------------------

@router.get("/users/{user_id}/quotas", response_model=list[QuotaOut], summary="查看用户额度")
async def list_quotas(
    user_id: uuid.UUID,
    session: DbSession,
    actor: Annotated[User, Depends(require_permission("quota.read"))],
) -> list[QuotaOut]:
    target = await session.get(User, user_id)
    if target is None:
        raise UserNotFoundError()
    await _guard_quota_scope(session, actor, target)
    quotas = await QuotaService(session).list_quotas(user_id)
    return [QuotaOut.model_validate(q) for q in quotas]


@router.put("/users/{user_id}/quotas", response_model=QuotaOut, summary="设置用户额度")
async def set_quota(
    user_id: uuid.UUID,
    body: QuotaSet,
    session: DbSession,
    actor: Annotated[User, Depends(require_permission("quota.manage"))],
) -> QuotaOut:
    target = await session.get(User, user_id)
    if target is None:
        raise UserNotFoundError()
    await _guard_quota_scope(session, actor, target)
    service = QuotaService(session)
    previous = await service.get_quota(user_id, body.quota_key, body.period)
    source = "unlimited" if body.unlimited else "manual"
    quota = await service.set_quota(
        user_id, body.quota_key, body.limit_value, body.period, limit_source=source
    )
    await AuditService(session).record(
        actor.id,
        "quota_set",
        subject_id=user_id,
        context={
            "quota_key": body.quota_key,
            "period": body.period,
            "before_limit": previous.limit_value if previous is not None else None,
            "before_source": previous.limit_source if previous is not None else None,
            "after_limit": body.limit_value,
            "after_source": source,
            "reason": body.reason,
        },
    )
    await session.flush()
    return QuotaOut.model_validate(quota)


@router.post("/users/{user_id}/quotas/adjust", response_model=QuotaOut, summary="按 10 万为单位调整额度")
async def adjust_quota(
    user_id: uuid.UUID,
    body: QuotaAdjust,
    session: DbSession,
    actor: Annotated[User, Depends(require_permission("quota.manage"))],
) -> QuotaOut:
    target = await session.get(User, user_id)
    if target is None:
        raise UserNotFoundError()
    await _guard_quota_scope(session, actor, target)
    service = QuotaService(session)
    previous = await service.get_quota(user_id, body.quota_key, body.period)
    quota = await service.adjust_quota(
        user_id,
        actor.id,
        action=body.action,
        units=body.units,
        reason=body.reason,
        quota_key=body.quota_key,
        period=body.period,
    )
    await AuditService(session).record(
        actor.id,
        "quota_adjust",
        subject_id=user_id,
        context={
            "quota_key": body.quota_key,
            "period": body.period,
            "action": body.action,
            "units": body.units,
            "unit_size": 100_000,
            "before_limit": None if previous is None or previous.limit_source == "unlimited" else previous.limit_value,
            "before_source": previous.limit_source if previous is not None else None,
            "after_limit": quota.limit_value,
            "reason": body.reason.strip(),
        },
    )
    await session.flush()
    return QuotaOut.model_validate(quota)


# ---------------------------------------------------------------------------
# 保护性校验
# ---------------------------------------------------------------------------

async def _guard_quota_scope(session: DbSession, actor: User, target: User) -> None:
    """分级控制：操作者最高角色等级必须不低于目标用户最高角色等级。

    super_admin（等级 100）可控制所有角色；admin（80）可控制 admin 及以下；
    逐级向下。等级不足时抛 HierarchyDeniedError。
    """
    authz = AuthorizationService(session)
    actor_roles = await authz.get_user_roles(actor.id)
    target_roles = await authz.get_user_roles(target.id)
    if max_role_rank(list(actor_roles)) < max_role_rank(list(target_roles)):
        raise HierarchyDeniedError()


async def _guard_last_super_admin(session: DbSession, user: User) -> None:
    from app.authorization.domain.catalog import SUPER_ADMIN_ROLE_CODE

    has_super = await session.scalar(
        select(UserRole.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(
            UserRole.user_id == user.id,
            Role.code == SUPER_ADMIN_ROLE_CODE,
            Role.status == "active",
        )
    )
    if has_super is None:
        return
    count = await session.scalar(
        select(func.count(UserRole.id))
        .join(Role, Role.id == UserRole.role_id)
        .where(Role.code == SUPER_ADMIN_ROLE_CODE, Role.status == "active")
    )
    if (count or 0) <= 1:
        from app.core.exceptions import SuperAdminProtectedError

        raise SuperAdminProtectedError()


async def _guard_role_change(session: DbSession, user: User, new_codes: set[str]) -> None:
    from app.authorization.domain.catalog import SUPER_ADMIN_ROLE_CODE

    has_super = await session.scalar(
        select(UserRole.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(UserRole.user_id == user.id, Role.code == SUPER_ADMIN_ROLE_CODE)
    )
    if has_super is not None and SUPER_ADMIN_ROLE_CODE not in new_codes:
        count = await session.scalar(
            select(func.count(UserRole.id))
            .join(Role, Role.id == UserRole.role_id)
            .where(Role.code == SUPER_ADMIN_ROLE_CODE, Role.status == "active")
        )
        if (count or 0) <= 1:
            from app.core.exceptions import SuperAdminProtectedError

            raise SuperAdminProtectedError()
