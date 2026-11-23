"""角色与用户权限管理。"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authorization.domain.catalog import (
    PERMISSION_BY_CODE,
    SUPER_ADMIN_ROLE_CODE,
    role_grant_rank,
)
from app.core.exceptions import (
    HierarchyDeniedError,
    InvalidRequestError,
    PermissionDeniedError,
    RoleNotFoundError,
    SuperAdminProtectedError,
)
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

    async def set_role_permissions(
        self,
        role_id: uuid.UUID | str,
        codes: set[str],
        *,
        grantable: frozenset[str] | None = None,
        actor_rank: int | None = None,
    ) -> None:
        """覆盖角色权限。

        ``grantable`` / ``actor_rank`` 是「谁在改」的上下文，接口层必须传：

        - ``actor_rank``：操作者的最高角色等级。不允许改动等级高于自己的角色，
          否则 admin 可以给自己的上级（或平级）扩权，分级控制形同虚设。
        - ``grantable``：操作者**可以转授**的权限码集合（见
          ``AuthorizationService.get_delegatable_permissions``）。被授权者只能享受
          授权，不能把它再下放；super_admin 的集合是全部权限。

        保留项：角色上已经存在、但当前操作者不可转授的权限允许「原样保留」，
        这样上级授予的权限不会因为下级管理员保存一次表单就被静默删除；要收回
        只能由可转授的人（或 super_admin）操作。
        """
        role = await self.get_role(role_id)
        if role.code == SUPER_ADMIN_ROLE_CODE:
            raise SuperAdminProtectedError()
        unknown = codes - set(PERMISSION_BY_CODE)
        if unknown:
            raise InvalidRequestError(f"未知权限: {', '.join(sorted(unknown))}")
        if actor_rank is not None and role_grant_rank(role.code) > actor_rank:
            raise HierarchyDeniedError()
        if grantable is not None:
            existing = await self.get_role_permissions(role_id)
            over = sorted(codes - set(grantable) - existing)
            if over:
                raise PermissionDeniedError(
                    "以下权限不在你的可转授范围内，需要更高权限的管理员分配: "
                    + ", ".join(over)
                )
        perms = list(
            await self.session.scalars(select(Permission).where(Permission.code.in_(codes)))
        )
        existing_rows = list(
            await self.session.scalars(select(RolePermission).where(RolePermission.role_id == role_id))
        )
        for row in existing_rows:
            await self.session.delete(row)
        # 先落库删除，再插入，避免同 key 的 INSERT 先于 DELETE 触发唯一约束冲突。
        await self.session.flush()
        for perm in perms:
            self.session.add(RolePermission(role_id=role_id, permission_id=perm.id))
        # 标记为「已手工配置」：启动时的 seed 之后不再为这个角色补默认权限，
        # 否则管理员取消掉的勾选会在下次重启时被加回来。
        role.permissions_customized = True
        await self.session.flush()

    async def assign_role_to_user(self, user_id: uuid.UUID | str, role_id: uuid.UUID | str) -> None:
        role = await self.get_role(role_id)
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
        *,
        grantable: frozenset[str] | None = None,
    ) -> None:
        """给单个用户直接允许 / 拒绝某个权限。

        ``grantable`` 与角色配置同一条转授权边界：操作者只能新授予自己可转授的
        权限；已经存在的直接授权可以改判（allow ↔ deny），否则下级管理员连收回
        自己给过的授权都做不到。
        """
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
        if row is None and grantable is not None and code not in grantable:
            from app.core.exceptions import PermissionDeniedError

            raise PermissionDeniedError(
                f"没有转授 {code} 的权限，需要更高权限的管理员分配"
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
