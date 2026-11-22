"""FastAPI 入口。"""

from __future__ import annotations

import asyncio
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
from app.core.database.lifecycle import shutdown_data_backends, startup_data_backends
from app.core.exceptions import AppError, InternalError, InvalidRequestError, error_payload
from app.core.logging import setup_logging
from app.agents.runtime import AgentRuntime
from app.providers.base import LLMProvider
from app.providers.manager import ProviderManager
from app.services.chat_service import ChatService
from app.services.prompt_service import PromptService
from app.services.tool_service import ToolService
from app.data.oss_client import oss_client_from_settings
from app.data.store import DataStore, resolve_store_root
from app.mcp.grants import MemoryGrantStore, SqlGrantStore, bind_grants
from app.mcp.keys import validate_key_config
from app.mcp.pool import production_pool
from app.tools.mcp_client import McpSession, register_mcp_tools
from app.tools.registry import build_agent_registry, build_default_registry
from app.tools.web_client import DeepSeekWebClient

logger = logging.getLogger("muzi_llm")


def create_app(
    settings: Settings | None = None,
    provider: LLMProvider | None = None,
    web_transport: Any | None = None,
) -> FastAPI:
    runtime_settings = settings or load_settings()
    setup_logging(runtime_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # 同一进程内重启 lifespan（测试 / 热重载）时，恢复实时事件的订阅能力。
        from app.content.events import reset as reset_content_events
        from app.services.recharge_events import reset as reset_recharge_events
        from app.services.shutdown import install as install_shutdown_hook

        reset_recharge_events()
        reset_content_events()
        # uvicorn 会先等所有在途连接结束才执行 lifespan shutdown，SSE 长连接永远
        # 等不到头，所以必须在信号阶段就把它们打断（见 app/services/shutdown.py）。
        install_shutdown_hook()
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
        agent_registry = build_agent_registry(
            search_calls=runtime_settings.web_search_max_calls,
            fetch_calls=runtime_settings.web_fetch_max_calls,
        )
        mcp_session: McpSession | None = None
        agent_runtime: AgentRuntime | None = None
        mcp_pool = production_pool(runtime_settings)
        app.state.mcp_pool = mcp_pool
        try:
            if runtime_settings.mcp_enabled:
                mcp_session = McpSession(runtime_settings)
                try:
                    mcp_tools = await mcp_session.start()
                    register_mcp_tools(agent_registry, mcp_session, mcp_tools, runtime_settings)
                except Exception:
                    logger.error("mcp startup failed")
                    await mcp_session.aclose()
                    mcp_session = None
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
                registry=agent_registry,
                web_client=web_client,
                data_store=data_store,
            )
            app.state.agent_runtime = agent_runtime
            raw_skills = runtime_settings.skills_root.strip()
            if raw_skills:
                from pathlib import Path

                from app.skills.activation_store import ActivationStore
                from app.skills.adapters import SkillPromptAdapter
                from app.skills.audit import SkillAuditService
                from app.skills.orchestrator import SkillOrchestrator
                from app.skills.registry import FileSkillRegistry

                skill_root = Path(raw_skills)
                if not skill_root.is_absolute():
                    skill_root = ROOT / skill_root
                skill_registry = FileSkillRegistry(skill_root, allow_system=True)
                skill_runtime = SkillPromptAdapter(
                    SkillOrchestrator(
                        skill_registry,
                        ActivationStore(skill_root / "runtime"),
                    )
                )
                # 启动对账：回收上次进程遗留的活跃快照，修正被泄漏的 active_count。
                try:
                    skill_runtime.orchestrator.reconcile()
                except Exception:
                    logger.warning("skill reconcile failed", exc_info=True)
                app.state.chat_service.skill_runtime = skill_runtime
                agent_runtime.skill_runtime = skill_runtime
                app.state.skill_runtime = skill_runtime
                app.state.skill_audit = SkillAuditService(skill_registry)
            app.state.web_client = web_client
            app.state.chat_service.ask_tools = ToolService(agent_registry, runtime_settings)
            app.state.chat_service.web_client = web_client
            app.state.chat_service.data_store = data_store
            app.state.chat_service.oss_client = oss_client_from_settings(runtime_settings)
            app.state.database_ready = False
            app.state.redis_ready = False
            validate_key_config(runtime_settings)
            production = runtime_settings.runtime_env.strip().lower() == "production"
            if production and not runtime_settings.database_startup_required:
                raise RuntimeError("生产环境必须开启数据库启动检查，拒绝使用内存授权")
            ready = await startup_data_backends(runtime_settings)
            if production and not ready:
                raise RuntimeError("生产环境数据库不可用，拒绝使用内存授权")
            if ready:
                from app.core.database.session import get_session_factory

                grant_store = SqlGrantStore(get_session_factory())
                try:
                    from app.authorization.seed import seed_authorization

                    async with get_session_factory()() as seed_session:
                        await seed_authorization(seed_session)
                        await seed_session.commit()
                except Exception:
                    logger.exception("authorization seed failed")
            else:
                grant_store = MemoryGrantStore()
            app.state.mcp_grants = grant_store
            app.state.mcp_grants_durable = ready
            bind_grants(grant_store, production=production)
            app.state.database_ready = ready
            app.state.redis_ready = ready
            sms_stop = asyncio.Event()
            sms_task: asyncio.Task[None] | None = None
            if runtime_settings.sms_receipt_enabled and runtime_settings.sms_mns_queue_name:
                from app.sms.receipts import run_receipt_loop

                sms_task = asyncio.create_task(run_receipt_loop(runtime_settings, sms_stop))
            app.state.sms_receipt_task = sms_task
            # 画布上传图的孤儿回收：引用计数归零且过了宽限期就删行 + OSS 对象。
            flow_gc_stop = asyncio.Event()
            flow_gc_task: asyncio.Task[None] | None = None
            if runtime_settings.flow_image_gc_enabled and ready:
                from app.services.flow_gc import run_flow_image_gc_loop

                flow_gc_task = asyncio.create_task(
                    run_flow_image_gc_loop(
                        runtime_settings,
                        app.state.chat_service.oss_client,
                        flow_gc_stop,
                    )
                )
            app.state.flow_gc_task = flow_gc_task
            # 内容任务队列在服务进程内直接消费：启动后端即启动任务消费，
            # 生命周期随 lifespan 管理，无需独立 worker 进程。
            content_worker = None
            if runtime_settings.content_worker_inprocess and ready:
                from app.content.inprocess import InProcessWorker

                content_worker = InProcessWorker(
                    max_async_tasks=runtime_settings.content_worker_max_async_tasks
                )
                await content_worker.start()
                app.state.content_worker = content_worker
            yield
        finally:
            # 先打断所有 SSE 长连接：否则 uvicorn 的优雅关闭会一直等它们结束，
            # 进程卡在「Waiting for connections to close」，端口不再响应新请求。
            from app.content.events import close_all as close_content_events
            from app.services.recharge_events import close_all as close_recharge_events

            close_recharge_events()
            close_content_events()
            content_worker = getattr(app.state, "content_worker", None)
            if content_worker is not None:
                await content_worker.stop()
                app.state.content_worker = None
            sms_task = getattr(app.state, "sms_receipt_task", None)
            if sms_task is not None:
                sms_task.cancel()
                try:
                    await sms_task
                except asyncio.CancelledError:
                    pass
            flow_gc_task = getattr(app.state, "flow_gc_task", None)
            if flow_gc_task is not None:
                flow_gc_stop.set()
                flow_gc_task.cancel()
                try:
                    await flow_gc_task
                except asyncio.CancelledError:
                    pass
            bind_grants(None)
            await mcp_pool.aclose()
            if mcp_session is not None:
                await mcp_session.aclose()
            if agent_runtime is not None:
                await agent_runtime.aclose()
            await web_client.aclose()
            await manager.shutdown()
            await shutdown_data_backends(enabled=runtime_settings.database_startup_required)

    app = FastAPI(
        title=runtime_settings.app_name,
        version=__version__,
        summary="DeepSeek Chat Completions 第一阶段服务",
        description=(
            "提供非流式对话、SSE、服务端提示词和受控 Function Calling。"
            "MCP 仅在服务端开启并列入白名单后可用。不包含 Harness、RAG、长期记忆或 Responses API。"
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
    from app.bridge.server import router as bridge_router

    app.include_router(bridge_router)
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
