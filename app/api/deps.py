"""依赖注入。"""

from __future__ import annotations

from fastapi import Request

from app.core.config import Settings
from app.core.exceptions import ConfigurationError
from app.services.chat_service import ChatService


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
