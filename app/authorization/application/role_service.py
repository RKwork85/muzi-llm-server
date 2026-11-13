"""角色与用户权限管理。"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authorization.domain.catalog import (
    PERMISSION_BY_CODE,
    SUPER_ADMIN_ROLE_CODE,
    permission_min_rank,
    role_grant_rank,
)
from app.core.exceptions import RoleNotFoundError, SuperAdminProtectedError
from app.models.rbac import (
    Permission,
    Role,
    RolePermission,
    UserPermission,
    UserRole,
)


class RoleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_roles(self) -> list[Role]:
        rows = await self.session.scalars(select(Role).order_by(Role.code))
        return list(rows)

    async def get_role(self, role_id: uuid.UUID | str) -> Role:
        role = await self.session.get(Role, role_id)
        if role is None:
            raise RoleNotFoundError()
        return role

    async def get_role_by_code(self, code: str) -> Role | None:
        return await self.session.scalar(select(Role).where(Role.code == code))

    async def create_role(self, code: str, name: str, description: str | None = None) -> Role:
        role = Role(code=code, name=name, description=description, is_system=False, status="active")
        self.session.add(role)
        await self.session.flush()
        return role

    async def update_role(
        self,
        role_id: uuid.UUID | str,
        *,
        name: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> Role:
        role = await self.get_role(role_id)
        if name is not None:
            role.name = name
        if description is not None:
            role.description = description
        if status is not None:
            if status == "disabled" and role.code == SUPER_ADMIN_ROLE_CODE:
                raise SuperAdminProtectedError()
            role.status = status
        await self.session.flush()
        return role

    async def delete_role(self, role_id: uuid.UUID | str) -> None:
        role = await self.get_role(role_id)
        if role.is_system:
            raise SuperAdminProtectedError()
        if role.code == SUPER_ADMIN_ROLE_CODE:
            raise SuperAdminProtectedError()
        await self.session.delete(role)
        await self.session.flush()

    async def get_role_permissions(self, role_id: uuid.UUID | str) -> set[str]:
        rows = await self.session.scalars(
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role_id)
        )
        return set(rows)

    async def set_role_permissions(self, role_id: uuid.UUID | str, codes: set[str]) -> None:
        role = await self.get_role(role_id)
        if role.code == SUPER_ADMIN_ROLE_CODE:
            raise SuperAdminProtectedError()
        unknown = codes - set(PERMISSION_BY_CODE)
        if unknown:
            from app.core.exceptions import InvalidRequestError

            raise InvalidRequestError(f"未知权限: {', '.join(sorted(unknown))}")
        rank = role_grant_rank(role.code)
        too_high = sorted(c for c in codes if permission_min_rank(c) > rank)
        if too_high:
            from app.core.exceptions import InvalidRequestError

            raise InvalidRequestError(f"权限超出角色等级: {', '.join(too_high)}")
        perms = list(
            await self.session.scalars(select(Permission).where(Permission.code.in_(codes)))
        )
        existing = list(
            await self.session.scalars(select(RolePermission).where(RolePermission.role_id == role_id))
        )
        for row in existing:
            await self.session.delete(row)
        # 先落库删除，再插入，避免同 key 的 INSERT 先于 DELETE 触发唯一约束冲突。
        await self.session.flush()
        for perm in perms:
            self.session.add(RolePermission(role_id=role_id, permission_id=perm.id))
        await self.session.flush()

    async def assign_role_to_user(self, user_id: uuid.UUID | str, role_id: uuid.UUID | str) -> None:
        exists = await self.session.scalar(
            select(UserRole.id).where(UserRole.user_id == user_id, UserRole.role_id == role_id)
        )
        if exists is None:
            self.session.add(UserRole(user_id=user_id, role_id=role_id))
            await self.session.flush()

    async def remove_role_from_user(self, user_id: uuid.UUID | str, role_id: uuid.UUID | str) -> None:
        role = await self.get_role(role_id)
        if role.code == SUPER_ADMIN_ROLE_CODE and await self._is_last_active_super_admin(user_id):
            raise SuperAdminProtectedError()
        rows = await self.session.scalars(
            select(UserRole).where(UserRole.user_id == user_id, UserRole.role_id == role_id)
        )
        for row in rows:
            await self.session.delete(row)
        await self.session.flush()

    async def _is_last_active_super_admin(self, user_id: uuid.UUID | str) -> bool:
        has = await self.session.scalar(
            select(UserRole.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(
                UserRole.user_id == user_id,
                Role.code == SUPER_ADMIN_ROLE_CODE,
                Role.status == "active",
            )
        )
        if has is None:
            return False
        count = await self.session.scalar(
            select(UserRole.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(Role.code == SUPER_ADMIN_ROLE_CODE, Role.status == "active")
        )
        return (count or 0) <= 1


class UserPermissionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_user_permissions(self, user_id: uuid.UUID | str) -> list[tuple[str, str]]:
        rows = await self.session.execute(
            select(Permission.code, UserPermission.grant_type)
            .join(UserPermission, UserPermission.permission_id == Permission.id)
            .where(UserPermission.user_id == user_id)
            .order_by(Permission.code)
        )
        return [(code, grant_type) for code, grant_type in rows]

    async def set_user_permission(
        self,
        user_id: uuid.UUID | str,
        code: str,
        grant_type: str,
    ) -> None:
        if code not in PERMISSION_BY_CODE:
            from app.core.exceptions import InvalidRequestError

            raise InvalidRequestError(f"未知权限: {code}")
        perm = await self.session.scalar(select(Permission).where(Permission.code == code))
        if perm is None:
            from app.core.exceptions import PermissionNotFoundError

            raise PermissionNotFoundError()
        row = await self.session.scalar(
            select(UserPermission).where(
                UserPermission.user_id == user_id, UserPermission.permission_id == perm.id
            )
        )
        if row is None:
            self.session.add(
                UserPermission(user_id=user_id, permission_id=perm.id, grant_type=grant_type)
            )
        else:
            row.grant_type = grant_type
        await self.session.flush()

    async def remove_user_permission(self, user_id: uuid.UUID | str, code: str) -> None:
        perm = await self.session.scalar(select(Permission).where(Permission.code == code))
        if perm is None:
            return
        rows = await self.session.scalars(
            select(UserPermission).where(
                UserPermission.user_id == user_id, UserPermission.permission_id == perm.id
            )
        )
        for row in rows:
            await self.session.delete(row)
        await self.session.flush()
