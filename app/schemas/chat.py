"""对话请求与响应。客户端不能提交 system、tool 或可执行工具定义。"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import Usage

_PROMPT_NAME = re.compile(r"^[a-z0-9_]+$")
_VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# 文档里同时提供两份请求体：完整字段用于查看原始结构，最简请求用于直接执行。
CHAT_COMPLETION_FULL_EXAMPLE: dict[str, object] = {
    "messages": [{"role": "user", "content": "解释一下 FastAPI 的依赖注入"}],
    "prompt_name": "default",
    "prompt_variables": {},
    "model": "deepseek-flash",
    "temperature": 0.7,
    "max_tokens": 1024,
    "thinking": {"type": "disabled"},
    "reasoning_effort": "low",
    "stream": False,
    "response_format": {"type": "text"},
}
CHAT_COMPLETION_EXAMPLE: dict[str, object] = {
    "messages": [{"role": "user", "content": "解释一下 FastAPI 的依赖注入"}],
    "prompt_name": "default",
}
JSON_COMPLETION_EXAMPLE: dict[str, object] = {
    "messages": [{"role": "user", "content": "请输出 json"}],
    "prompt_name": "json_assistant",
    "prompt_variables": {"task": "用 answer 字段回答 1+1"},
    "response_format": {"type": "json_object"},
}
STREAM_CHAT_FULL_EXAMPLE: dict[str, object] = {
    "messages": [{"role": "user", "content": "用三句话介绍 SSE"}],
    "prompt_name": "default",
    "prompt_variables": {},
    "model": "deepseek-flash",
    "temperature": 0.7,
    "max_tokens": 1024,
    "thinking": {"type": "disabled"},
    "reasoning_effort": "low",
    "stream": True,
    "response_format": {"type": "text"},
}
STREAM_CHAT_EXAMPLE: dict[str, object] = {
    "messages": [{"role": "user", "content": "用三句话介绍 SSE"}],
    "prompt_name": "default",
}
TOOL_CALL_FULL_EXAMPLE: dict[str, object] = {
    "messages": [
        {
            "role": "user",
            "content": "请调用 calculator 计算 23 * 19。不要心算，最终答案使用工具返回的数字。",
        }
    ],
    "prompt_name": "calculator",
    "prompt_variables": {},
    "model": "deepseek-flash",
    "temperature": 0,
    "max_tokens": 256,
    "thinking": {"type": "disabled"},
    "reasoning_effort": "low",
    "max_tool_rounds": 3,
    "tool_names": ["calculator", "get_current_time"],
}
TOOL_CALL_EXAMPLE: dict[str, object] = {
    "messages": [
        {
            "role": "user",
            "content": "请调用 calculator 计算 23 * 19。不要心算，最终答案使用工具返回的数字。",
        }
    ],
    "prompt_name": "calculator",
    "max_tool_rounds": 3,
    "thinking": {"type": "disabled"},
}


class ThinkingOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["enabled", "disabled"]


class ResponseFormat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text", "json_object"]


class ChatMessageIn(BaseModel):
    """只接受用户和助手的历史文本。system 由服务端提示词决定，tool 由服务端写入。"""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=32000)

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("消息内容不能为空")
        return value


class ChatRequestBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessageIn] = Field(min_length=1, max_length=50)
    prompt_name: str = "default"
    prompt_variables: dict[str, str] = Field(default_factory=dict)
    model: str | None = Field(default=None, max_length=128)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=393216)
    thinking: ThinkingOptions | None = None
    reasoning_effort: Literal["none", "low", "high", "max"] | None = None

    @field_validator("prompt_name")
    @classmethod
    def _prompt_name(cls, value: str) -> str:
        if not _PROMPT_NAME.fullmatch(value):
            raise ValueError("提示词名称无效")
        return value

    @field_validator("prompt_variables")
    @classmethod
    def _prompt_variables(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 20:
            raise ValueError("变量过多")
        for key, item in value.items():
            if not _VARIABLE_NAME.fullmatch(key):
                raise ValueError("变量名非法")
            if len(item) > 500:
                raise ValueError("变量值过长")
        return value


class ChatCompletionRequest(ChatRequestBase):
    stream: bool | None = False
    response_format: ResponseFormat | None = None


class StreamChatRequest(ChatRequestBase):
    stream: bool | None = True
    response_format: ResponseFormat | None = None
    mode: Literal["ask", "plan", "agent"] = "ask"
    skill_names: list[str] = Field(default_factory=list, max_length=8)
    skill_mode: Literal["auto", "recommended", "explicit", "disabled"] | None = None
    explicit_skill_ids: list[str] = Field(default_factory=list, max_length=3)

    @field_validator("skill_names")
    @classmethod
    def _skill_names(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for name in value:
            if not _PROMPT_NAME.fullmatch(name) or len(name) > 32:
                raise ValueError("技能名称无效")
            if name not in cleaned:
                cleaned.append(name)
        return cleaned

    @field_validator("explicit_skill_ids")
    @classmethod
    def _explicit_skill_ids(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for item in value:
            if not re.fullmatch(r"sk_[a-z0-9]{8,32}", item) or item in seen:
                raise ValueError("技能标识无效")
            seen.append(item)
        return seen


class ToolCallRequest(ChatRequestBase):
    max_tool_rounds: int | None = Field(default=None, ge=1, le=10)
    tool_names: list[str] | None = Field(default=None, max_length=10)

    @field_validator("tool_names")
    @classmethod
    def _tool_names(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        for name in value:
            if not _TOOL_NAME.fullmatch(name):
                raise ValueError("工具名称不合法")
        if len(set(value)) != len(value):
            raise ValueError("工具名称重复")
        return value


class AssistantMessageOut(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str | None = None
    reasoning_content: str | None = None


class ChoiceOut(BaseModel):
    index: int = 0
    message: AssistantMessageOut
    finish_reason: str


class ChatCompletionResponse(BaseModel):
    request_id: str
    id: str
    model: str
    choices: list[ChoiceOut]
    usage: Usage | None = None
    finish_reason: str
    tool_rounds: int | None = None
