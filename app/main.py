"""FastAPI 入口。"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import __version__
from app.api.health import router as health_router
from app.api.v1.router import router as api_router
from app.core.config import ROOT, Settings, load_settings
from app.core.context import get_request_id, normalize_request_id, reset_request_id, set_request_id
from app.core.exceptions import AppError, InternalError, InvalidRequestError, error_payload
from app.core.logging import setup_logging
from app.agents.runtime import AgentRuntime
from app.providers.base import LLMProvider
from app.providers.manager import ProviderManager
from app.services.chat_service import ChatService
from app.services.prompt_service import PromptService
from app.services.tool_service import ToolService
from app.data.store import DataStore, resolve_store_root
from app.tools.registry import build_agent_registry, build_default_registry
from app.tools.web_client import DeepSeekWebClient

logger = logging.getLogger("qs_llm")


def create_app(
    settings: Settings | None = None,
    provider: LLMProvider | None = None,
    web_transport: Any | None = None,
) -> FastAPI:
    runtime_settings = settings or load_settings()
    setup_logging(runtime_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        manager = ProviderManager(runtime_settings, app.state.provider_override)
        await manager.startup()
        prompts = PromptService()
        registry = build_default_registry()
        tool_service = ToolService(registry, runtime_settings)
        app.state.provider_manager = manager
        app.state.provider = manager.provider
        app.state.prompt_service = prompts
        app.state.tool_service = tool_service
        app.state.chat_service = ChatService(
            settings=runtime_settings,
            provider=manager.provider,
            prompts=prompts,
            tools=tool_service,
        )
        web_client = DeepSeekWebClient(runtime_settings, transport=web_transport)
        await web_client.startup()
        store_root = resolve_store_root(runtime_settings)
        if store_root != ROOT:
            store_root.mkdir(parents=True, exist_ok=True)
        data_store = DataStore.from_settings(runtime_settings)
        data_store.cleanup_expired()
        app.state.data_store = data_store
        agent_runtime = AgentRuntime(
            settings=runtime_settings,
            provider=manager.provider,
            prompts=prompts,
            registry=build_agent_registry(
                search_calls=runtime_settings.web_search_max_calls,
                fetch_calls=runtime_settings.web_fetch_max_calls,
            ),
            web_client=web_client,
            data_store=data_store,
        )
        app.state.agent_runtime = agent_runtime
        app.state.web_client = web_client
        try:
            yield
        finally:
            await agent_runtime.aclose()
            await web_client.aclose()
            await manager.shutdown()

    app = FastAPI(
        title=runtime_settings.app_name,
        version=__version__,
        summary="DeepSeek Chat Completions 第一阶段服务",
        description=(
            "提供非流式对话、SSE、服务端提示词和受控 Function Calling。"
            "本阶段不包含 Harness、MCP、RAG、长期记忆或 Responses API。"
        ),
        lifespan=lifespan,
    )
    app.state.settings = runtime_settings
    app.state.provider_override = provider

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        request_id = normalize_request_id(request.headers.get("x-request-id"))
        request.state.request_id = request_id
        token = set_request_id(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            reset_request_id(token)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "http request_id=%s method=%s path=%s status=%s latency_ms=%.1f",
            request_id,
            request.method,
            _public_path(request.url.path),
            response.status_code,
            (time.perf_counter() - started) * 1000,
        )
        return response

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        request_id = _request_id_from(request)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc, request_id),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        del exc
        request_id = _request_id_from(request)
        return JSONResponse(
            status_code=422,
            content=error_payload(InvalidRequestError(), request_id),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "unhandled error request_id=%s type=%s",
            _request_id_from(request),
            type(exc).__name__,
        )
        return JSONResponse(
            status_code=500,
            content=error_payload(InternalError(), _request_id_from(request)),
        )

    app.include_router(health_router)
    app.include_router(api_router, prefix="/api/v1")
    return app


def _public_path(path: str) -> str:
    marker = "/files/downloads/"
    if marker in path:
        return path.split(marker, 1)[0] + marker + "[redacted]"
    return path


def _request_id_from(request: Request) -> str:
    state_id = getattr(request.state, "request_id", None)
    if isinstance(state_id, str) and state_id:
        return state_id
    current = get_request_id()
    return current if current != "unknown" else "unknown"


app = create_app()
