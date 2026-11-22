"""画布「文字编辑节点」的补全请求 / 响应契约。

这是一个轻量的非流式文本补全接口，和对话页的 ``/chat/completions`` 分开：
- 不进入会话、不挂工具、不做多轮编排，一次请求就是一次「写完即返回」；
- 统一把 ``usage`` 与 ``elapsed_ms`` 带回去，节点据此展示 token 消耗和执行时间。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TextCompletionMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=20000)


class TextCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # deepseek：DeepSeek 官方 OpenAI 兼容接口；ark：火山方舟豆包系列
    provider: Literal["deepseek", "ark"] = "deepseek"
    # 留空时使用该通道的默认模型
    model: str = Field(default="", max_length=128)
    messages: list[TextCompletionMessage] = Field(min_length=1, max_length=16)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=32768)


class TextCompletionUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class TextCompletionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = ""
    provider: Literal["deepseek", "ark"]
    model: str
    content: str
    finish_reason: str = "stop"
    usage: TextCompletionUsage = Field(default_factory=TextCompletionUsage)
    # 服务端实测耗时（毫秒）：从前端请求进入后端到拿到完整正文
    elapsed_ms: int = 0
