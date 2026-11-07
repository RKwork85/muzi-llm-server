"""构造发给 DeepSeek 的 messages。裁剪只按完整工具组进行。"""

from __future__ import annotations

import json
from typing import Any

from app.agents.policy import ExecutionPolicy
from app.agents.provider_adapter import NormalizedCompletion


class ContextError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class ContextManager:
    def __init__(
        self,
        *,
        system_prompt: str,
        user_message: str,
        policy: ExecutionPolicy,
    ) -> None:
        self._system = system_prompt
        self._user = user_message
        self._policy = policy
        self._groups: list[list[dict[str, Any]]] = []
        self._open: list[dict[str, Any]] | None = None
        self._expected_ids: list[str] = []

    def append_assistant(self, completion: NormalizedCompletion) -> None:
        if self._open is not None:
            raise ContextError("AGENT_CONTEXT_INVALID", "上一个工具调用尚未闭合")
        message: dict[str, Any] = {
            "role": "assistant",
            "content": completion.content or "",
        }
        if completion.reasoning_content is not None:
            message["reasoning_content"] = completion.reasoning_content
        if completion.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in completion.tool_calls
            ]
            self._open = [message]
            self._expected_ids = [call.id for call in completion.tool_calls]
            return
        self._groups.append([message])

    def append_tool_result(self, tool_call_id: str, content: str) -> None:
        if self._open is None or not self._expected_ids:
            raise ContextError("AGENT_CONTEXT_INVALID", "没有等待结果的工具调用")
        expected = self._expected_ids.pop(0)
        if tool_call_id != expected:
            raise ContextError("AGENT_CONTEXT_INVALID", "工具结果与调用标识不匹配")
        self._open.append({"role": "tool", "tool_call_id": tool_call_id, "content": content})
        if not self._expected_ids:
            self._groups.append(self._open)
            self._open = None

    def render(self) -> list[dict[str, Any]]:
        if self._open is not None:
            raise ContextError("AGENT_CONTEXT_INVALID", "工具调用没有完整结果")
        prefix = self._prefix()
        groups = list(self._groups)
        while groups and not _fits(prefix, groups, self._policy):
            groups.pop(0)
        if not _fits(prefix, groups, self._policy):
            raise ContextError("AGENT_CONTEXT_LIMIT_REACHED", "上下文超出预算")
        self._groups = groups
        return _flatten(prefix, groups)

    def public_messages(self) -> list[dict[str, Any]]:
        return [_without_reasoning(item) for item in self.render()]

    def _prefix(self) -> list[dict[str, Any]]:
        return [
            {"role": "system", "content": self._system},
            {"role": "user", "content": self._user},
        ]


def _fits(
    prefix: list[dict[str, Any]],
    groups: list[list[dict[str, Any]]],
    policy: ExecutionPolicy,
) -> bool:
    messages = _flatten(prefix, groups)
    if len(messages) > policy.max_context_messages:
        return False
    encoded = json.dumps(messages, ensure_ascii=False)
    return len(encoded) <= policy.max_context_chars


def _flatten(
    prefix: list[dict[str, Any]],
    groups: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    messages = list(prefix)
    for group in groups:
        messages.extend(group)
    return messages


def _without_reasoning(message: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in message.items() if key != "reasoning_content"}
