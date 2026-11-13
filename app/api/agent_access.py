"""Agent 入口的登录与执行权限。调用方必须在启动运行之前使用。"""

from __future__ import annotations

from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.exc import SQLAlchemyError

from app.authorization.application.authorization_service import AuthorizationService
from app.core.database.session import get_session_factory
from app.core.exceptions import DatabaseUnavailableError, LoginRequiredError, PermissionDeniedError
from app.models.tables import User
from app.services.auth_service import AuthService


def _token(credentials: HTTPAuthorizationCredentials | None) -> str:
    if credentials is None or not credentials.credentials.strip():
        raise LoginRequiredError()
    return credentials.credentials


async def authenticate_request_user(credentials: HTTPAuthorizationCredentials | None) -> str:
    """解析服务端认可的用户。令牌无效或库不可用时不返回默认身份。"""
    raw = _token(credentials)
    try:
        factory = get_session_factory()
    except RuntimeError:
        raise DatabaseUnavailableError() from None
    try:
        async with factory() as session:
            owner = await AuthService(session).user_for_token(raw)
            if owner is None:
                raise LoginRequiredError()
            return str(owner.id)
    except (LoginRequiredError, PermissionDeniedError, DatabaseUnavailableError):
        raise
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None


async def require_agent_operator(credentials: HTTPAuthorizationCredentials | None) -> User:
    """登录且具备 agent.execute 后才返回用户。数据库不可用时拒绝。"""
    raw = _token(credentials)
    try:
        factory = get_session_factory()
    except RuntimeError:
        raise DatabaseUnavailableError() from None
    try:
        async with factory() as session:
            owner = await AuthService(session).user_for_token(raw)
            if owner is None:
                raise LoginRequiredError()
            await AuthorizationService(session).require_permission(owner.id, "agent.execute")
            await session.commit()
            return owner
    except (LoginRequiredError, PermissionDeniedError, DatabaseUnavailableError):
        raise
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None
