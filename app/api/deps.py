"""依赖注入。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings
from app.core.database.deps import DbSession
from app.core.exceptions import ConfigurationError, LoginRequiredError
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
