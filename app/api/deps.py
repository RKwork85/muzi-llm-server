"""依赖注入。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings
from app.core.database.deps import DbSession
from app.core.database.session import get_session_factory
from app.core.exceptions import ConfigurationError, DatabaseUnavailableError, LoginRequiredError
from app.models.tables import User
from app.services.auth_service import AuthService
from app.services.chat_service import ChatService

_bearer = HTTPBearer(auto_error=False)


def get_settings_dep(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise ConfigurationError()
    return settings


def get_chat_service(request: Request) -> ChatService:
    service = getattr(request.app.state, "chat_service", None)
    if service is None:
        raise ConfigurationError()
    return service


def get_agent_runtime(request: Request):
    from app.agents.runtime import AgentRuntime

    runtime = getattr(request.app.state, "agent_runtime", None)
    if not isinstance(runtime, AgentRuntime):
        raise ConfigurationError()
    return runtime


async def get_authenticated_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: DbSession,
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise LoginRequiredError()
    user = await AuthService(session).user_for_token(credentials.credentials)
    if user is None:
        raise LoginRequiredError()
    return user


async def get_stream_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    """长连接（SSE）专用鉴权：只解析出 user_id，不占用请求级数据库连接。

    普通接口用 `get_authenticated_user`，它借的是请求级 session，事务要等响应
    结束才提交。SSE 的响应在客户端断开前永远不会结束，若沿用那条依赖，一条
    连接池连接会被整条流的生命周期占住；连接池只有 5+10 条，几个页签就能把
    池子抽干，之后所有请求都会卡在等连接上（表现为前端「页面卡死」）。
    这里自己开一个短生命周期 session，取到 id 立刻关闭。
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise LoginRequiredError()
    try:
        factory = get_session_factory()
    except RuntimeError:
        raise DatabaseUnavailableError() from None
    async with factory() as session:
        user = await AuthService(session).user_for_token(credentials.credentials)
        if user is None:
            raise LoginRequiredError()
        return str(user.id)
