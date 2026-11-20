"""统一权限决策入口（RBAC + ABAC）。

身份认证回答「你是谁」，权限系统回答「你能做什么」，资源隔离回答「你能操作谁的数据」。

决策规则（默认拒绝、显式授权、拒绝优先）：
- 用户不存在或非 active → 拒绝
- super_admin → 隐式全部放行（不依赖 role_permissions，避免误删）
- 显式 deny（user_permissions）→ 拒绝，覆盖角色授权
- 角色授权或用户显式 allow → 允许
- 其余 → 拒绝

资源访问（ABAC）：RBAC 判定「能否执行某类操作」，资源归属/共享/显式授权
判定「能否对当前这个具体资源执行」。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authorization.domain.catalog import (
    ADMIN_ROLE_CODE,
    PERMISSION_BY_CODE,
    SUPER_ADMIN_ROLE_CODE,
)
from app.core.exceptions import PermissionDeniedError
from app.models.mixins import utcnow
from app.models.rbac import (
    Permission,
    ResourceGrant,
    ResourceShare,
    Role,
    RolePermission,
    UserPermission,
    UserRole,
)
from app.models.tables import User


@dataclass(frozen=True)
class PermissionSnapshot:
    """用户的有效权限与数据范围快照，供 /me 与后台预览使用。"""

    roles: tuple[str, ...]
    permissions: frozenset[str]
    data_scope: str


class AuthorizationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # RBAC
    # ------------------------------------------------------------------

    async def has_permission(
        self,
        user_id: uuid.UUID | str,
        permission: str,
        resource_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """统一权限判断入口。resource_id / context 预留给 ABAC 扩展。"""
        del resource_id, context
        user = await self.session.get(User, user_id)
        if user is None or user.status != "active":
            return False
        if await self._is_super_admin(user_id):
            return True
        denied, granted = await self._resolve(user_id)
        if permission in denied:
            return False
        return permission in granted

    async def require_permission(
        self,
        user_id: uuid.UUID | str,
        permission: str,
        resource_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """无权限时抛出 PermissionDeniedError，并尽力记录拒绝审计。"""
        if await self.has_permission(user_id, permission, resource_id, context):
            return
        await _audit_denial_best_effort(
            actor_id=str(user_id),
            permission=permission,
            resource_id=resource_id,
            context=context,
        )
        raise PermissionDeniedError()

    async def get_effective_permissions(self, user_id: uuid.UUID | str) -> set[str]:
        if await self._is_super_admin(user_id):
            return set(PERMISSION_BY_CODE)
        denied, granted = await self._resolve(user_id)
        return granted - denied

    async def get_user_roles(self, user_id: uuid.UUID | str) -> list[str]:
        rows = await self.session.scalars(
            select(Role.code)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id, Role.status == "active")
            .order_by(Role.code)
        )
        return list(rows)

    async def snapshot(self, user_id: uuid.UUID | str) -> PermissionSnapshot:
        """单个用户的有效权限快照。

        与 ``snapshot_many`` 保持语义一致，用于单用户场景（/me、详情、写操作后回显）。
        """
        roles = await self.get_user_roles(user_id)
        if SUPER_ADMIN_ROLE_CODE in roles:
            return PermissionSnapshot(
                roles=tuple(roles),
                permissions=frozenset(PERMISSION_BY_CODE),
                data_scope="all",
            )
        denied, granted = await self._resolve(user_id)
        return PermissionSnapshot(
            roles=tuple(roles),
            permissions=frozenset(granted - denied),
            data_scope="all" if ADMIN_ROLE_CODE in roles else "self",
        )

    async def snapshot_many(
        self, user_ids: list[uuid.UUID | str]
    ) -> dict[str, PermissionSnapshot]:
        """批量计算多个用户的有效权限快照，避免列表页 N+1 查询。

        一次查出所有目标用户的活动角色、角色权限与用户级授权，在内存中分组，
        语义与逐用户 ``snapshot`` 完全一致。返回 key 为 ``str(user_id)``。
        """
        if not user_ids:
            return {}
        ids: list[uuid.UUID] = [
            u if isinstance(u, uuid.UUID) else uuid.UUID(str(u)) for u in user_ids
        ]
        keys = [str(u) for u in ids]

        # 1) 所有目标用户的活动角色（role id + code）
        role_rows = await self.session.execute(
            select(UserRole.user_id, Role.id, Role.code)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id.in_(ids), Role.status == "active")
            .order_by(Role.code)
        )
        roles_by_user: dict[str, list[str]] = {k: [] for k in keys}
        role_ids_by_user: dict[str, list[uuid.UUID]] = {k: [] for k in keys}
        all_role_ids: set[uuid.UUID] = set()
        for uid, role_id, code in role_rows:
            uk = str(uid)
            roles_by_user.setdefault(uk, []).append(code)
            role_ids_by_user.setdefault(uk, []).append(role_id)
            all_role_ids.add(role_id)

        # 2) 这些角色携带的权限
        perms_by_role: dict[uuid.UUID, set[str]] = {}
        if all_role_ids:
            perm_rows = await self.session.execute(
                select(RolePermission.role_id, Permission.code)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(RolePermission.role_id.in_(all_role_ids))
            )
            for role_id, code in perm_rows:
                perms_by_role.setdefault(role_id, set()).add(code)

        # 3) 用户级显式授权/拒绝
        now = utcnow()
        user_perm_rows = await self.session.execute(
            select(
                UserPermission.user_id,
                Permission.code,
                UserPermission.grant_type,
                UserPermission.expires_at,
            )
            .join(Permission, Permission.id == UserPermission.permission_id)
            .where(UserPermission.user_id.in_(ids))
        )
        user_granted: dict[str, set[str]] = {k: set() for k in keys}
        user_denied: dict[str, set[str]] = {k: set() for k in keys}
        for uid, code, grant_type, expires_at in user_perm_rows:
            if expires_at is not None and expires_at <= now:
                continue
            uk = str(uid)
            if grant_type == "deny":
                user_denied.setdefault(uk, set()).add(code)
            else:
                user_granted.setdefault(uk, set()).add(code)

        # 4) 组装快照
        result: dict[str, PermissionSnapshot] = {}
        for uk in keys:
            roles = tuple(roles_by_user.get(uk, []))
            if SUPER_ADMIN_ROLE_CODE in roles:
                permissions = frozenset(PERMISSION_BY_CODE)
                data_scope = "all"
            else:
                granted: set[str] = set()
                for role_id in role_ids_by_user.get(uk, []):
                    granted |= perms_by_role.get(role_id, set())
                granted |= user_granted.get(uk, set())
                permissions = frozenset(granted - user_denied.get(uk, set()))
                data_scope = "all" if ADMIN_ROLE_CODE in roles else "self"
            result[uk] = PermissionSnapshot(
                roles=roles,
                permissions=permissions,
                data_scope=data_scope,
            )
        return result

    async def get_data_scope(self, user_id: uuid.UUID | str) -> str:
        if await self._is_super_admin(user_id):
            return "all"
        if await self._has_role_code(user_id, ADMIN_ROLE_CODE):
            return "all"
        return "self"

    async def is_super_admin(self, user_id: uuid.UUID | str) -> bool:
        return await self._is_super_admin(user_id)

    # ------------------------------------------------------------------
    # ABAC：资源归属 + 共享 + 显式授权
    # ------------------------------------------------------------------

    async def has_resource_access(
        self,
        user_id: uuid.UUID | str,
        permission: str,
        resource_type: str,
        resource_id: str,
        owner_id: uuid.UUID | str | None = None,
    ) -> bool:
        """RBAC 判定 + 资源归属/共享/显式授权判定。"""
        if not await self.has_permission(user_id, permission):
            return False
        if owner_id is not None and str(owner_id) == str(user_id):
            return True
        if await self._shared_to(user_id, resource_type, resource_id, permission):
            return True
        return await self._granted(user_id, resource_type, resource_id)

    async def require_resource_access(
        self,
        user_id: uuid.UUID | str,
        permission: str,
        resource_type: str,
        resource_id: str,
        owner_id: uuid.UUID | str | None = None,
    ) -> None:
        if not await self.has_resource_access(user_id, permission, resource_type, resource_id, owner_id):
            raise PermissionDeniedError()

    # ------------------------------------------------------------------
    # 内部查询
    # ------------------------------------------------------------------

    async def _is_super_admin(self, user_id: uuid.UUID | str) -> bool:
        return await self._has_role_code(user_id, SUPER_ADMIN_ROLE_CODE)

    async def _has_role_code(self, user_id: uuid.UUID | str, code: str) -> bool:
        found = await self.session.scalar(
            select(Role.id)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id, Role.code == code, Role.status == "active")
            .limit(1)
        )
        return found is not None

    async def _resolve(self, user_id: uuid.UUID | str) -> tuple[set[str], set[str]]:
        """返回 (denied, granted) 权限码集合。"""
        role_ids = await self.session.scalars(
            select(Role.id)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id, Role.status == "active")
        )
        granted: set[str] = set()
        if role_ids:
            granted = set(
                await self.session.scalars(
                    select(Permission.code)
                    .join(RolePermission, RolePermission.permission_id == Permission.id)
                    .where(RolePermission.role_id.in_(role_ids))
                )
            )
        denied: set[str] = set()
        now = utcnow()
        rows = await self.session.execute(
            select(Permission.code, UserPermission.grant_type, UserPermission.expires_at)
            .join(UserPermission, UserPermission.permission_id == Permission.id)
            .where(UserPermission.user_id == user_id)
        )
        for code, grant_type, expires_at in rows:
            if expires_at is not None and expires_at <= now:
                continue
            if grant_type == "deny":
                denied.add(code)
            else:
                granted.add(code)
        return denied, granted

    async def _shared_to(
        self,
        user_id: uuid.UUID | str,
        resource_type: str,
        resource_id: str,
        permission: str,
    ) -> bool:
        now = utcnow()
        found = await self.session.scalar(
            select(ResourceShare.id).where(
                ResourceShare.grantee_id == user_id,
                ResourceShare.resource_type == resource_type,
                ResourceShare.resource_id == resource_id,
                ResourceShare.permission == permission,
                ResourceShare.revoked_at.is_(None),
            )
        )
        if found is None:
            return False
        expires = await self.session.scalar(
            select(ResourceShare.expires_at).where(ResourceShare.id == found)
        )
        return expires is None or expires > now

    async def _granted(
        self,
        user_id: uuid.UUID | str,
        resource_type: str,
        resource_id: str,
    ) -> bool:
        now = utcnow()
        grant = await self.session.scalar(
            select(ResourceGrant).where(
                ResourceGrant.user_id == user_id,
                ResourceGrant.resource_type == resource_type,
                ResourceGrant.resource_id == resource_id,
                ResourceGrant.grant_type == "allow",
                ResourceGrant.revoked_at.is_(None),
            )
        )
        if grant is None:
            return False
        return grant.expires_at is None or grant.expires_at > now


async def _audit_denial_best_effort(
    *,
    actor_id: str,
    permission: str,
    resource_id: str | None,
    context: dict[str, Any] | None,
) -> None:
    """拒绝决策用独立事务落库，避免请求事务回滚时丢失审计。失败不阻断主流程。"""
    try:
        from app.core.database.session import get_session_factory

        from app.models.rbac import AccessAuditLog

        factory = get_session_factory()
    except RuntimeError:
        return
    try:
        async with factory() as session:
            session.add(
                AccessAuditLog(
                    actor_id=uuid.UUID(actor_id),
                    action="authorize",
                    permission=permission,
                    resource_id=resource_id,
                    decision="deny",
                    outcome="failure",
                    context_json={"reason": "permission_denied", "context": context or {}},
                )
            )
            await session.commit()
    except Exception:
        pass
