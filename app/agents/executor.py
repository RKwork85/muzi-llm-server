"""独立工具执行器。失败变成结构化结果，不改第一阶段工具服务。"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
import logging
import re
import time
from collections.abc import Collection
from typing import Any

from pydantic import BaseModel, ValidationError

from app.agents.models import ToolExecutionResult, ToolExecutionStatus
from app.agents.policy import RunClock
from app.tools.registry import ToolRegistry, ToolSpec
from app.tools.web_client import CURRENT_TOOL_BUDGET, CURRENT_WEB_CLIENT, WebCallError, web_error_content

logger = logging.getLogger("qs_llm")

_CALL_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TRUNCATED = "...[truncated]"
MAX_TOOL_ARGUMENTS_CHARS = 8192
MAX_JSON_DEPTH = 32


class ToolExecutor:
    """按注册表执行一次工具调用。服务端名单和本次名单都必须允许。"""

    def __init__(self, registry: ToolRegistry, server_allowed: Collection[str]) -> None:
        self._registry = registry
        self._server_allowed = frozenset(server_allowed)
        self._sync_executor: Any = None
        self._network_gate: asyncio.Semaphore | None = None
        self._web_client: Any = None

    async def execute(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        arguments: str,
        allowlist: Collection[str],
        clock: RunClock,
        request_id: str | None = None,
        now: float | None = None,
    ) -> ToolExecutionResult:
        started = time.perf_counter()
        public_name = tool_name if _TOOL_NAME.fullmatch(tool_name) else "invalid"
        public_call_id = tool_call_id if _CALL_ID.fullmatch(tool_call_id) else "invalid"
        run_allowed = frozenset(allowlist)

        def finish(
            status: ToolExecutionStatus,
            content: str,
            error_code: str | None,
        ) -> ToolExecutionResult:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return ToolExecutionResult(
                tool_call_id=public_call_id,
                tool_name=public_name,
                status=status,
                content=content,
                error_code=error_code,
                duration_ms=max(0, elapsed_ms),
            )

        if public_call_id == "invalid":
            return finish(
                ToolExecutionStatus.REJECTED,
                _error_content(public_name, "INVALID_TOOL_ARGUMENTS", "工具参数无效", clock),
                "INVALID_TOOL_ARGUMENTS",
            )
        if public_name == "invalid" or self._registry.get(tool_name) is None:
            return finish(
                ToolExecutionStatus.REJECTED,
                _error_content(public_name, "TOOL_NOT_REGISTERED", "工具未注册", clock),
                "TOOL_NOT_REGISTERED",
            )

        spec = self._registry.get(tool_name)
        assert spec is not None
        if spec.risk_level != "low" or tool_name not in self._server_allowed:
            return finish(
                ToolExecutionStatus.REJECTED,
                _error_content(public_name, "TOOL_NOT_ALLOWED", "工具不在允许列表中", clock),
                "TOOL_NOT_ALLOWED",
            )
        if tool_name not in run_allowed:
            return finish(
                ToolExecutionStatus.REJECTED,
                _error_content(public_name, "TOOL_NOT_ALLOWED", "工具不在允许列表中", clock),
                "TOOL_NOT_ALLOWED",
            )

        try:
            payload = _parse_object(arguments)
            args = spec.args_model.model_validate(payload)
        except (json.JSONDecodeError, ValidationError, ValueError, TypeError, RecursionError, UnicodeError):
            return finish(
                ToolExecutionStatus.REJECTED,
                _error_content(public_name, "INVALID_TOOL_ARGUMENTS", "工具参数无效", clock),
                "INVALID_TOOL_ARGUMENTS",
            )

        budget = _tool_budget(clock, spec, now)
        if budget <= 0:
            return finish(
                ToolExecutionStatus.TIMED_OUT,
                _error_content(public_name, "TOOL_DEADLINE_EXCEEDED", "已超过 Agent 总时限", clock),
                "TOOL_DEADLINE_EXCEEDED",
            )

        acquired = False
        if spec.requires_network:
            if self._network_gate is None:
                return finish(
                    ToolExecutionStatus.REJECTED,
                    _error_content(public_name, "TOOL_NOT_ALLOWED", "工具不在允许列表中", clock),
                    "TOOL_NOT_ALLOWED",
                )
            try:
                await asyncio.wait_for(self._network_gate.acquire(), timeout=budget)
            except TimeoutError:
                return finish(
                    ToolExecutionStatus.TIMED_OUT,
                    _error_content(public_name, "TOOL_TIMEOUT", "工具执行超时", clock),
                    "TOOL_TIMEOUT",
                )
            except asyncio.CancelledError:
                raise
            acquired = True
            budget = _tool_budget(clock, spec, None)
            if budget <= 0:
                self._network_gate.release()
                return finish(
                    ToolExecutionStatus.TIMED_OUT,
                    _error_content(public_name, "TOOL_DEADLINE_EXCEEDED", "已超过 Agent 总时限", clock),
                    "TOOL_DEADLINE_EXCEEDED",
                )

        budget_token = CURRENT_TOOL_BUDGET.set(budget)
        try:
            task = asyncio.create_task(self._invoke(spec, args), name=f"tool:{spec.name}")
            try:
                done, _pending = await asyncio.wait({task}, timeout=budget)
            except asyncio.CancelledError:
                _detach(task)
                raise
            if task not in done:
                _detach(task)
                logger.error(
                    "tool timeout request_id=%s name=%s",
                    request_id or "-",
                    spec.name,
                )
                return finish(
                    ToolExecutionStatus.TIMED_OUT,
                    _error_content(public_name, "TOOL_TIMEOUT", "工具执行超时", clock),
                    "TOOL_TIMEOUT",
                )

            try:
                raw = task.result()
            except asyncio.CancelledError:
                raise
            except WebCallError as exc:
                return finish(
                    ToolExecutionStatus.FAILED,
                    web_error_content(public_name, exc.code, retryable=exc.retryable),
                    exc.code,
                )
            except ValueError:
                return finish(
                    ToolExecutionStatus.REJECTED,
                    _error_content(public_name, "INVALID_TOOL_ARGUMENTS", "工具参数无效", clock),
                    "INVALID_TOOL_ARGUMENTS",
                )
            except Exception:
                logger.error(
                    "tool execution failed request_id=%s name=%s",
                    request_id or "-",
                    spec.name,
                )
                return finish(
                    ToolExecutionStatus.FAILED,
                    _error_content(public_name, "TOOL_EXECUTION_ERROR", "工具执行失败", clock),
                    "TOOL_EXECUTION_ERROR",
                )

            text = _json_text(raw)
            if text is None:
                logger.error(
                    "tool execution failed request_id=%s name=%s",
                    request_id or "-",
                    spec.name,
                )
                return finish(
                    ToolExecutionStatus.FAILED,
                    _error_content(public_name, "TOOL_EXECUTION_ERROR", "工具执行失败", clock),
                    "TOOL_EXECUTION_ERROR",
                )
            limit = clock.policy.max_tool_result_chars
            if spec.max_result_chars is not None:
                limit = min(limit, spec.max_result_chars)
            bounded = _bounded_tool_content(spec.name, text, limit)
            if bounded is None:
                return finish(
                    ToolExecutionStatus.FAILED,
                    _error_content(public_name, "TOOL_RESULT_TOO_LARGE", "工具结果超出长度上限", clock),
                    "TOOL_RESULT_TOO_LARGE",
                )
            return finish(ToolExecutionStatus.SUCCEEDED, bounded, None)
        finally:
            CURRENT_TOOL_BUDGET.reset(budget_token)
            if acquired and self._network_gate is not None:
                self._network_gate.release()

    def bind_sync_executor(self, executor: Any) -> None:
        """Runtime 注入有界线程池。超时后的线程仍占着池里的工人，直到函数返回。"""
        self._sync_executor = executor

    def bind_network_gate(self, gate: asyncio.Semaphore) -> None:
        """联网工具的独立并发闸门。排队时间计入本次工具预算。"""
        self._network_gate = gate

    def bind_web_client(self, client: Any) -> None:
        self._web_client = client

    async def _invoke(self, spec: ToolSpec, args: BaseModel) -> Any:
        handler = spec.handler
        if inspect.iscoroutinefunction(handler):
            if not spec.requires_network:
                return await handler(args)
            token = CURRENT_WEB_CLIENT.set(self._web_client)
            try:
                return await handler(args)
            finally:
                CURRENT_WEB_CLIENT.reset(token)
        pool = self._sync_executor
        context = contextvars.copy_context()
        if pool is None:
            return await asyncio.to_thread(context.run, handler, args)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(pool, context.run, handler, args)


def _tool_budget(clock: RunClock, spec: ToolSpec, now: float | None) -> float:
    budget = clock.tool_timeout(now)
    if spec.timeout_s is not None:
        budget = min(budget, spec.timeout_s)
    return budget


def _parse_object(arguments: str) -> dict[str, Any]:
    if not isinstance(arguments, str):
        raise TypeError("arguments")
    if len(arguments) > MAX_TOOL_ARGUMENTS_CHARS:
        raise ValueError("arguments")
    _reject_deep_json(arguments)

    def _reject_constant(token: str) -> None:
        raise ValueError(token)

    try:
        payload = json.loads(arguments, parse_constant=_reject_constant)
    except RecursionError as exc:
        raise ValueError("arguments") from exc
    if not isinstance(payload, dict):
        raise ValueError("arguments")
    return payload


def _reject_deep_json(arguments: str) -> None:
    depth = 0
    max_depth = 0
    in_string = False
    escape = False
    for char in arguments:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
            if depth > max_depth:
                max_depth = depth
            if max_depth > MAX_JSON_DEPTH:
                raise ValueError("arguments")
        elif char in "}]":
            depth = max(0, depth - 1)


def _json_text(raw: Any) -> str | None:
    if isinstance(raw, str):
        return raw
    try:
        return json.dumps(raw, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return None


def _error_content(name: str, code: str, message: str, clock: RunClock) -> str:
    limit = clock.policy.max_tool_result_chars
    candidates = (
        {
            "source": "server_tool",
            "name": name,
            "error": {"code": code, "message": message},
        },
        {"source": "server_tool", "error": {"code": code, "message": "工具执行失败"}},
        {"source": "server_tool", "error": {"code": code}},
    )
    chosen = json.dumps(candidates[-1], ensure_ascii=False)
    for payload in candidates:
        text = json.dumps(payload, ensure_ascii=False)
        if len(text) <= limit:
            return text
        chosen = text
    return chosen


def _bounded_tool_content(name: str, raw: str, limit: int) -> str | None:
    """返回合法 JSON。信封放不进上限时返回 None，由调用方记为失败。"""

    def dump(result: str) -> str:
        return json.dumps(
            {"source": "server_tool", "name": name, "result": result},
            ensure_ascii=False,
        )

    full = dump(raw)
    if len(full) <= limit:
        return full
    if len(dump(_TRUNCATED)) > limit:
        return None
    best = dump(_TRUNCATED)
    low = 0
    high = len(raw)
    while low <= high:
        mid = (low + high) // 2
        candidate = dump(raw[:mid] + _TRUNCATED)
        if len(candidate) <= limit:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    try:
        json.loads(best)
    except json.JSONDecodeError:
        return None
    return best


def _detach(task: asyncio.Task[Any]) -> None:
    """超时或取消后丢弃后续结果。工作线程可能仍在运行，但不能再报成功。"""
    task.add_done_callback(_consume_quietly)
    if not task.done():
        task.cancel()


def _consume_quietly(task: asyncio.Task[Any]) -> None:
    try:
        task.exception()
    except asyncio.CancelledError:
        return
    except Exception:
        return
