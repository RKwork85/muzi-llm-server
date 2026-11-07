"""校验并执行已注册工具。模型参数在执行前不可信。"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
from typing import Any

from pydantic import ValidationError

from app.core.config import Settings
from app.core.context import get_request_id
from app.core.exceptions import (
    InvalidModelResponseError,
    InvalidToolArgumentsError,
    ToolExecutionError,
    ToolNotAllowedError,
)
from app.providers.base import ToolCall
from app.tools.registry import ToolRegistry, ToolSpec
from app.tools.web_client import WebCallError

logger = logging.getLogger("qs_llm")
_CALL_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class ToolService:
    def __init__(self, registry: ToolRegistry, settings: Settings) -> None:
        self.registry = registry
        self._settings = settings

    def schemas_for(self, names: list[str] | None) -> list[dict[str, Any]]:
        if names is None:
            specs = self.registry.all_tools()
        else:
            specs = []
            for name in names:
                spec = self._allowed(name)
                specs.append(spec)
        return [spec.openai_schema() for spec in specs]

    async def execute(
        self,
        calls: list[ToolCall],
        *,
        allowed: frozenset[str] | None = None,
    ) -> list[dict[str, str]]:
        if not calls or len(calls) > 5:
            raise InvalidModelResponseError()
        validated = []
        for call in calls:
            item = self._validate(call)
            if allowed is not None and item[1].name not in allowed:
                raise ToolNotAllowedError()
            validated.append(item)
        async def run_one(call: ToolCall, spec: ToolSpec, args: Any) -> dict[str, str]:
            return {
                "role": "tool",
                "tool_call_id": call.id,
                "content": await self._run(spec, args),
            }

        return list(await asyncio.gather(*(run_one(call, spec, args) for call, spec, args in validated)))

    def _allowed(self, name: str) -> ToolSpec:
        spec = self.registry.get(name)
        if spec is None or spec.risk_level != "low":
            raise ToolNotAllowedError()
        return spec

    def _validate(self, call: ToolCall) -> tuple[ToolCall, ToolSpec, Any]:
        if not _CALL_ID.fullmatch(call.id):
            raise InvalidModelResponseError()
        spec = self._allowed(call.name)
        try:
            payload = json.loads(call.arguments or "")
        except json.JSONDecodeError:
            raise InvalidToolArgumentsError() from None
        if not isinstance(payload, dict):
            raise InvalidToolArgumentsError()
        try:
            args = spec.args_model.model_validate(payload)
        except ValidationError:
            raise InvalidToolArgumentsError() from None
        return call, spec, args

    async def _run(self, spec: ToolSpec, args: Any) -> str:
        timeout = spec.timeout_s if spec.timeout_s is not None else self._settings.tool_timeout_seconds
        try:
            if inspect.iscoroutinefunction(spec.handler):
                raw = await asyncio.wait_for(spec.handler(args), timeout=timeout)
            else:
                raw = await asyncio.wait_for(asyncio.to_thread(spec.handler, args), timeout=timeout)
        except WebCallError as exc:
            raw = exc.code
        except TimeoutError:
            logger.error(
                "tool timeout request_id=%s name=%s",
                get_request_id(),
                spec.name,
            )
            raise ToolExecutionError() from None
        except ValueError:
            raise InvalidToolArgumentsError() from None
        except ZeroDivisionError:
            raise ToolExecutionError() from None
        except Exception:
            logger.error(
                "tool execution failed request_id=%s name=%s",
                get_request_id(),
                spec.name,
            )
            raise ToolExecutionError() from None
        if not isinstance(raw, str):
            raw = json.dumps(raw, ensure_ascii=False)
        return self._bounded_content(spec.name, raw)

    def _bounded_content(self, name: str, raw: str) -> str:
        """截断结果本身，并保持回传内容是合法 JSON。"""
        limit = self._settings.tool_result_max_chars
        marker = "...[truncated]"

        def dump(result: str) -> str:
            return json.dumps(
                {"source": "server_tool", "name": name, "result": result},
                ensure_ascii=False,
            )

        full = dump(raw)
        if len(full) <= limit:
            return full
        if len(dump(marker)) > limit:
            return full[: max(0, limit - len(marker))] + marker
        best = dump(marker)
        low = 0
        high = len(raw)
        while low <= high:
            mid = (low + high) // 2
            candidate = dump(raw[:mid] + marker)
            if len(candidate) <= limit:
                best = candidate
                low = mid + 1
            else:
                high = mid - 1
        return best
