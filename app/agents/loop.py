"""模型与工具的有限循环。不处理 HTTP、SSE 或任务存储。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from app.agents.artifacts import absorb_tool_output
from app.agents.context import ContextError, ContextManager
from app.agents.executor import ToolExecutor, _error_content
from app.agents.models import TaskStatus, UsageSnapshot
from app.agents.policy import ExecutionPolicy, RunClock
from app.agents.provider_adapter import (
    NormalizedCompletion,
    ProviderFailure,
    complete_for_agent,
)
from app.providers.base import LLMProvider, ProviderChatRequest
from app.tools.registry import ToolRegistry

_CALL_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_BAD_FINISH = {
    "length": "MODEL_OUTPUT_LIMIT",
    "content_filter": "MODEL_CONTENT_FILTER",
    "insufficient_system_resource": "MODEL_RESOURCE_EXHAUSTED",
    "aborted": "MODEL_ABORTED",
}
_LIMIT_CODES = frozenset(
    {
        "AGENT_DEADLINE_EXCEEDED",
        "AGENT_STEP_LIMIT",
        "AGENT_TOOL_LIMIT",
        "AGENT_CONTEXT_LIMIT_REACHED",
    }
)


class EventSink(Protocol):
    def publish(self, event_type: str, data: dict[str, object]) -> None:
        """发布已脱敏事件。实现不得阻塞循环。"""


@dataclass
class LoopResult:
    status: TaskStatus
    answer: str | None
    steps: int
    tool_calls: int
    error_code: str | None
    usage: UsageSnapshot | None
    artifacts: list[dict[str, object]] | None = None


class AgentLoop:
    def __init__(
        self,
        *,
        provider: LLMProvider,
        executor: ToolExecutor,
        context: ContextManager,
        policy: ExecutionPolicy,
        clock: RunClock,
        registry: ToolRegistry,
        model: str,
        thinking: str,
        request_id: str,
        events: EventSink | None = None,
        artifact_max_count: int = 5,
        artifact_max_chars: int = 20000,
    ) -> None:
        self._provider = provider
        self._executor = executor
        self._context = context
        self._policy = policy
        self._clock = clock
        self._registry = registry
        self._model = model
        self._thinking = thinking
        self._request_id = request_id
        self._events = events
        self._seen_ids: set[str] = set()
        self._call_counts: dict[str, int] = {}
        self._collected: list[dict[str, object]] = []
        self._artifact_max_count = artifact_max_count
        self._artifact_max_chars = artifact_max_chars

    async def run(self) -> LoopResult:
        steps = 0
        tool_count = 0
        usage: UsageSnapshot | None = None
        while True:
            if self._clock.remaining() <= 0:
                return self._done(TaskStatus.LIMIT_REACHED, None, steps, tool_count, "AGENT_DEADLINE_EXCEEDED", usage)
            if steps >= self._policy.max_steps:
                return self._done(TaskStatus.LIMIT_REACHED, None, steps, tool_count, "AGENT_STEP_LIMIT", usage)
            steps += 1
            self._emit("agent.step.started", {"step": steps})
            try:
                messages = self._context.render()
            except ContextError as exc:
                status = TaskStatus.LIMIT_REACHED if exc.code in _LIMIT_CODES else TaskStatus.FAILED
                return self._done(status, None, steps, tool_count, exc.code, usage)
            self._emit("agent.model.started", {"step": steps})
            try:
                completion = await complete_for_agent(
                    self._provider,
                    self._request(messages),
                    self._clock,
                )
            except ProviderFailure as exc:
                status = TaskStatus.LIMIT_REACHED if exc.code == "AGENT_DEADLINE_EXCEEDED" else TaskStatus.FAILED
                return self._done(status, None, steps, tool_count, exc.code, usage)
            usage = _add_usage(usage, completion.usage)
            self._emit("agent.model.completed", {"step": steps, "finish_reason": completion.finish_reason})
            if completion.finish_reason in _BAD_FINISH:
                return self._done(TaskStatus.FAILED, None, steps, tool_count, _BAD_FINISH[completion.finish_reason], usage)
            if completion.finish_reason == "unknown":
                return self._done(TaskStatus.FAILED, None, steps, tool_count, "MODEL_FINISH_UNKNOWN", usage)
            if completion.finish_reason == "tool_calls":
                outcome = await self._run_tools(completion, steps, tool_count, usage)
                if isinstance(outcome, LoopResult):
                    return outcome
                tool_count = outcome
                continue
            if completion.tool_calls:
                return self._done(TaskStatus.FAILED, None, steps, tool_count, "INVALID_MODEL_RESPONSE", usage)
            text = (completion.content or "").strip()
            if completion.finish_reason == "stop" and text:
                return self._done(TaskStatus.COMPLETED, completion.content, steps, tool_count, None, usage)
            return self._done(TaskStatus.FAILED, None, steps, tool_count, "EMPTY_MODEL_RESPONSE", usage)

    async def _run_tools(
        self,
        completion: NormalizedCompletion,
        steps: int,
        tool_count: int,
        usage: UsageSnapshot | None,
    ) -> LoopResult | int:
        calls = completion.tool_calls
        if not calls:
            return self._done(TaskStatus.FAILED, None, steps, tool_count, "INVALID_MODEL_RESPONSE", usage)
        if self._thinking == "enabled" and completion.reasoning_content is None:
            return self._done(TaskStatus.FAILED, None, steps, tool_count, "MODEL_REASONING_MISSING", usage)
        ids = [call.id for call in calls]
        if any(not _CALL_ID.fullmatch(call_id) for call_id in ids) or len(ids) != len(set(ids)):
            return self._done(TaskStatus.FAILED, None, steps, tool_count, "INVALID_MODEL_RESPONSE", usage)
        if any(call_id in self._seen_ids for call_id in ids):
            return self._done(TaskStatus.FAILED, None, steps, tool_count, "INVALID_MODEL_RESPONSE", usage)
        if len(calls) > self._policy.max_tool_calls_per_step or tool_count + len(calls) > self._policy.max_tool_calls:
            return self._done(TaskStatus.LIMIT_REACHED, None, steps, tool_count, "AGENT_TOOL_LIMIT", usage)
        self._seen_ids.update(ids)
        self._context.append_assistant(completion)
        for call in calls:
            fresh: list[dict[str, object]] = []
            if self._clock.remaining() <= 0:
                result_content, status_name, error_code = _deadline_tool_content(call.name, self._clock)
            elif self._over_call_limit(call.name):
                tool_count += 1
                result_content = _error_content(
                    call.name,
                    "TOOL_CALL_LIMIT",
                    "已达到该工具的调用次数上限",
                    self._clock,
                )
                status_name = "failed"
                error_code = "TOOL_CALL_LIMIT"
            else:
                self._emit("agent.tool.started", {"tool_name": call.name, "step": steps})
                executed = await self._executor.execute(
                    tool_call_id=call.id,
                    tool_name=call.name,
                    arguments=call.arguments,
                    allowlist=self._policy.allowed_tools,
                    clock=self._clock,
                    request_id=self._request_id,
                )
                self._count_call(call.name)
                tool_count += 1
                result_content = executed.content
                status_name = executed.status.value
                error_code = executed.error_code
                if error_code is None:
                    result_content, fresh, limit_code = absorb_tool_output(
                        result_content,
                        already=len(self._collected),
                        max_count=self._artifact_max_count,
                        max_chars=self._artifact_max_chars,
                    )
                    if limit_code:
                        error_code = limit_code
                        status_name = "failed"
                        fresh = []
                    elif fresh:
                        self._collected.extend(fresh)
            self._context.append_tool_result(call.id, result_content)
            event_name = "agent.tool.completed" if error_code is None else "agent.tool.failed"
            event_data: dict[str, object] = {
                "tool_name": call.name,
                "status": status_name,
                "error_code": error_code or "",
                "step": steps,
            }
            if fresh and error_code is None:
                event_data["artifact_id"] = fresh[0]["id"]
                event_data["artifact_type"] = fresh[0]["type"]
                event_data["artifact_size"] = sum(len(str(item["content"])) for item in fresh)
                event_data["artifact_count"] = len(fresh)
            self._emit(event_name, event_data)
        return tool_count

    def _request(self, messages: list[dict[str, object]]) -> ProviderChatRequest:
        tools = _allowed_schemas(self._registry, self._policy.allowed_tools)
        return ProviderChatRequest(
            model=self._model,
            messages=messages,
            thinking={"type": self._thinking},
            tools=tools or None,
            tool_choice="auto" if tools else None,
        )

    def _emit(self, event_type: str, data: dict[str, object]) -> None:
        if self._events is not None:
            self._events.publish(event_type, data)

    def _done(
        self,
        status: TaskStatus,
        answer: str | None,
        steps: int,
        tool_calls: int,
        error_code: str | None,
        usage: UsageSnapshot | None,
    ) -> LoopResult:
        result = _result(status, answer, steps, tool_calls, error_code, usage)
        result.artifacts = list(self._collected) or None
        return result

    def _over_call_limit(self, name: str) -> bool:
        spec = self._registry.get(name)
        if spec is None or spec.max_calls_per_run is None:
            return False
        return self._call_counts.get(name, 0) >= spec.max_calls_per_run

    def _count_call(self, name: str) -> None:
        spec = self._registry.get(name)
        if spec is None or spec.max_calls_per_run is None:
            return
        self._call_counts[name] = self._call_counts.get(name, 0) + 1


def _allowed_schemas(registry: ToolRegistry, names: tuple[str, ...]) -> list[dict[str, object]]:
    schemas: list[dict[str, object]] = []
    for name in names:
        spec = registry.get(name)
        if spec is None or spec.risk_level != "low":
            continue
        schemas.append(spec.openai_schema())
    return schemas


def _deadline_tool_content(name: str, clock: RunClock) -> tuple[str, str, str]:
    payload = json.dumps(
        {
            "source": "server_tool",
            "name": name,
            "error": {"code": "TOOL_DEADLINE_EXCEEDED", "message": "已超过 Agent 总时限"},
        },
        ensure_ascii=False,
    )
    if len(payload) > clock.policy.max_tool_result_chars:
        payload = json.dumps(
            {"source": "server_tool", "error": {"code": "TOOL_DEADLINE_EXCEEDED"}},
            ensure_ascii=False,
        )
    return payload, "timed_out", "TOOL_DEADLINE_EXCEEDED"


def _add_usage(current: UsageSnapshot | None, incoming: UsageSnapshot | None) -> UsageSnapshot | None:
    if incoming is None:
        return current
    if current is None:
        return incoming
    return UsageSnapshot(
        prompt_tokens=current.prompt_tokens + incoming.prompt_tokens,
        completion_tokens=current.completion_tokens + incoming.completion_tokens,
        total_tokens=current.total_tokens + incoming.total_tokens,
    )


def _result(
    status: TaskStatus,
    answer: str | None,
    steps: int,
    tool_calls: int,
    error_code: str | None,
    usage: UsageSnapshot | None,
) -> LoopResult:
    return LoopResult(
        status=status,
        answer=answer,
        steps=steps,
        tool_calls=tool_calls,
        error_code=error_code,
        usage=usage,
    )
