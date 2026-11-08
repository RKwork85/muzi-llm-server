"""一次 Agent 运行的协调者。消息裁剪和 HTTP 细节不放在这里。"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from app.agents.context import ContextManager
from app.agents.events import EventPublisher
from app.agents.executor import ToolExecutor
from app.agents.loop import AgentLoop, LoopResult
from app.agents.models import TERMINAL_TASK_STATUSES, AgentRequest, TaskStatus
from app.agents.policy import (
    ExecutionPolicy,
    PolicyError,
    RunClock,
    merge_execution_policy,
    server_policy_from_settings,
)
from app.agents.toolsets import agent_tool_names
from app.agents.provider_adapter import redact_exception
from app.agents.task_manager import TaskManager, TaskRecord
from app.core.config import Settings
from app.data.context import CURRENT_DATA_STORE, CURRENT_OSS_CLIENT, CURRENT_TASK_ID
from app.data.oss_client import oss_client_from_settings
from app.data.store import DataStore
from app.core.exceptions import AgentPolicyRejectedError, AgentTaskNotFoundError
from app.providers.base import LLMProvider
from app.services.prompt_service import PromptService
from app.tools.registry import ToolRegistry
from app.tools.url_policy import UrlLedger
from app.tools.web_client import DeepSeekWebClient
from app.tools.file_sandbox import FileDenied
from app.tools.file_tools import workspace_root
from app.mcp.execution import CURRENT_MCP_SCOPE, McpScope
from app.mcp.service import overlay_tools
from app.tools.stdio_access import CURRENT_MCP_USER
from app.tools.web_tools import CURRENT_SETTINGS, CURRENT_URL_LEDGER, UNTRUSTED_PROMPT
from app.tools.workspace_scope import CURRENT_WORKSPACE_SCOPE, is_user_scope

logger = logging.getLogger("qs_llm")

_TERMINAL_EVENT = {
    TaskStatus.COMPLETED: "agent.completed",
    TaskStatus.FAILED: "agent.failed",
    TaskStatus.CANCELLED: "agent.cancelled",
    TaskStatus.LIMIT_REACHED: "agent.limit_reached",
}


class AgentRuntime:
    def __init__(
        self,
        *,
        settings: Settings,
        provider: LLMProvider,
        prompts: PromptService,
        registry: ToolRegistry,
        web_client: DeepSeekWebClient | None = None,
        data_store: DataStore | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._prompts = prompts
        self._registry = registry
        self._web_client = web_client
        self._data_store = data_store
        self._oss_client = oss_client_from_settings(settings)
        self._tasks = TaskManager(
            max_active=settings.agent_max_active_tasks,
            ttl_seconds=settings.agent_task_ttl_seconds,
        )
        self._pool = ThreadPoolExecutor(
            max_workers=settings.agent_sync_tool_concurrency,
            thread_name_prefix="agent-tool",
        )
        self._network_gate = asyncio.Semaphore(settings.tools_network_concurrency)
        self._cleanup_timeout = settings.agent_cancel_cleanup_seconds

    @property
    def tasks(self) -> TaskManager:
        return self._tasks

    async def start(
        self,
        request: AgentRequest,
        request_id: str,
        *,
        workspace_scope: str | None = None,
        mcp_user_id: str | None = None,
        mcp_service: object | None = None,
    ) -> dict[str, object]:
        try:
            granted = agent_tool_names(
                self._registry,
                self._settings,
                request.toolsets,
                request.grants,
                mcp_user_id,
            )
            remote: list = []
            want_remote = mcp_service is not None and mcp_user_id is not None
            want_remote = want_remote and (request.toolsets is None or "mcp" in request.toolsets)
            if want_remote:
                try:
                    remote = await mcp_service.tool_specs(mcp_user_id)  # type: ignore[attr-defined]
                except Exception:
                    logger.info("remote mcp tools skipped")
                    remote = []
            if remote:
                granted = tuple(sorted(set(granted) | {spec.name for spec in remote}))
            server = server_policy_from_settings(self._settings, self._registry)
            policy = merge_execution_policy(
                server.model_copy(update={"allowed_tools": granted}),
                request,
            )
        except PolicyError as exc:
            raise AgentPolicyRejectedError(exc.code) from None
        prompt = self._prompts.render(request.prompt_name)
        selected = set(request.toolsets or [])
        sensitive = {
            "web_search",
            "web_fetch",
            "weather_query",
            "read_text_file",
            "search_text",
            "write_text_file",
            "write_docx",
            "delete_file",
        }
        if selected & {"research", "workspace", "data", "weather"} or set(granted) & sensitive:
            prompt = f"{prompt}\n\n{UNTRUSTED_PROMPT}"
        registry = overlay_tools(self._settings, self._registry, remote) if remote else self._registry
        record = self._tasks.create(request_id)
        record.publisher = EventPublisher(
            request_id=record.request_id,
            task_id=record.task_id,
            max_queue_size=policy.max_event_queue_size,
        )
        worker = asyncio.create_task(
            self._execute(record, request.message, prompt, policy, workspace_scope, mcp_user_id, registry),
            name=f"agent:{record.task_id}",
        )
        record.worker = worker
        return record.view()

    def get(self, task_id: str) -> dict[str, object]:
        return self._tasks.get(task_id).view()

    async def cancel(self, task_id: str) -> dict[str, object]:
        record = self._tasks.get(task_id)
        worker = record.worker
        if record.status in TERMINAL_TASK_STATUSES or not isinstance(worker, asyncio.Task):
            return record.view()
        worker.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(worker), timeout=self._cleanup_timeout)
        except TimeoutError:
            if self._tasks.finish_cancelled(record) and record.publisher is not None:
                record.publisher.publish("agent.cancelled", {"error_code": "AGENT_CANCELLED"})
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
        return record.view()

    def publisher_for(self, task_id: str) -> EventPublisher:
        record = self._tasks.get(task_id)
        if record.publisher is None:
            raise AgentTaskNotFoundError()
        return record.publisher

    async def aclose(self) -> None:
        workers = [
            record.worker
            for record in self._tasks.records()
            if isinstance(record.worker, asyncio.Task) and not record.worker.done()
        ]
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.wait(workers, timeout=self._cleanup_timeout)
        self._pool.shutdown(wait=False, cancel_futures=True)

    async def _execute(
        self,
        record: TaskRecord,
        message: str,
        prompt: str,
        policy: ExecutionPolicy,
        workspace_scope: str | None = None,
        mcp_user_id: str | None = None,
        registry: ToolRegistry | None = None,
    ) -> None:
        publisher = record.publisher
        if publisher is None:
            publisher = EventPublisher(
                request_id=record.request_id,
                task_id=record.task_id,
                max_queue_size=policy.max_event_queue_size,
            )
            record.publisher = publisher
        self._tasks.mark_running(record)
        publisher.publish("agent.started", {})
        settings_token = CURRENT_SETTINGS.set(self._settings)
        ledger_token = CURRENT_URL_LEDGER.set(UrlLedger(message))
        task_token = CURRENT_TASK_ID.set(record.task_id)
        store_token = CURRENT_DATA_STORE.set(self._data_store)
        oss_token = CURRENT_OSS_CLIENT.set(self._oss_client)
        scope_token = (
            CURRENT_WORKSPACE_SCOPE.set(workspace_scope) if workspace_scope is not None else None
        )
        user_token = CURRENT_MCP_USER.set(mcp_user_id)
        mcp_token = None
        if mcp_user_id is not None:
            mcp_token = CURRENT_MCP_SCOPE.set(
                McpScope(user_id=mcp_user_id, request_id=record.request_id, agent_run_id=record.task_id)
            )
        if workspace_scope is not None:
            self._prepare_workspace(record.request_id, workspace_scope)
        try:
            await self._run_loop(record, message, prompt, policy, publisher, registry or self._registry)
        finally:
            if mcp_token is not None:
                CURRENT_MCP_SCOPE.reset(mcp_token)
            CURRENT_MCP_USER.reset(user_token)
            if scope_token is not None:
                CURRENT_WORKSPACE_SCOPE.reset(scope_token)
            CURRENT_OSS_CLIENT.reset(oss_token)
            CURRENT_DATA_STORE.reset(store_token)
            CURRENT_TASK_ID.reset(task_token)
            CURRENT_SETTINGS.reset(settings_token)
            CURRENT_URL_LEDGER.reset(ledger_token)

    def _prepare_workspace(self, request_id: str, workspace_scope: str) -> None:
        if is_user_scope(workspace_scope):
            logger.info("workspace bound request_id=%s scope=%s", request_id, workspace_scope)
        else:
            logger.info("workspace unbound request_id=%s", request_id)
        try:
            workspace_root(self._settings)
        except FileDenied:
            return

    async def _run_loop(
        self,
        record: TaskRecord,
        message: str,
        prompt: str,
        policy: ExecutionPolicy,
        publisher: EventPublisher,
        registry: ToolRegistry,
    ) -> None:
        clock = RunClock.start(policy)
        context = ContextManager(system_prompt=prompt, user_message=message, policy=policy)
        executor = ToolExecutor(registry, policy.allowed_tools)
        executor.bind_sync_executor(self._pool)
        executor.bind_network_gate(self._network_gate)
        executor.bind_web_client(self._web_client)
        loop = AgentLoop(
            provider=self._provider,
            executor=executor,
            context=context,
            policy=policy,
            clock=clock,
            registry=registry,
            model=self._settings.deepseek_model,
            thinking=self._settings.deepseek_thinking,
            request_id=record.request_id,
            events=publisher,
            artifact_max_count=self._settings.agent_max_artifacts,
            artifact_max_chars=self._settings.agent_max_artifact_chars,
        )
        try:
            result = await loop.run()
            self._publish_terminal(record, result, publisher)
        except asyncio.CancelledError:
            if self._tasks.finish_cancelled(record):
                publisher.publish("agent.cancelled", {"error_code": "AGENT_CANCELLED"})
            raise
        except Exception as exc:
            logger.error(
                "agent run failed request_id=%s task_id=%s type=%s",
                record.request_id,
                record.task_id,
                redact_exception(exc),
            )
            failed = LoopResult(
                status=TaskStatus.FAILED,
                answer=None,
                steps=record.steps,
                tool_calls=record.tool_calls,
                error_code="AGENT_INTERNAL_ERROR",
                usage=None,
            )
            self._publish_terminal(record, failed, publisher)

    def _publish_terminal(self, record: TaskRecord, result: LoopResult, publisher: EventPublisher) -> None:
        accepted = self._tasks.finish(record, result)
        if not accepted:
            return
        data: dict[str, object] = {}
        if result.error_code:
            data["error_code"] = result.error_code
        data["status"] = result.status.value
        if publisher.dropped:
            data["dropped_events"] = publisher.dropped
        publisher.publish(_TERMINAL_EVENT[result.status], data)
