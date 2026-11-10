"""FastAPI 权限依赖。

用法：

    from app.authorization.dependencies import require_permission

    @router.get("/admin/users")
    async def list_users(user: Annotated[User, Depends(require_permission("user.read"))]): ...
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends

from app.api.deps import get_authenticated_user
from app.authorization.application.authorization_service import AuthorizationService
from app.core.database.deps import DbSession
from app.models.tables import User


def require_permission(permission: str) -> Callable[..., object]:
    """返回一个 FastAPI 依赖：校验当前登录用户具备指定权限。"""

    async def _dep(
        user: Annotated[User, Depends(get_authenticated_user)],
        session: DbSession,
    ) -> User:
        await AuthorizationService(session).require_permission(user.id, permission)
        return user

    return _dep


def get_authorization_service(
    session: DbSession,
) -> AuthorizationService:
    return AuthorizationService(session)
