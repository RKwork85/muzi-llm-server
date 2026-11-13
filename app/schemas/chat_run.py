"""一轮 Ask 的只读运行详情。不参与扣减，也不回放事件正文。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ChatRunCall(BaseModel):
    settlement_key: str
    model: str | None = None
    status: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None


class ChatRunToolUsage(BaseModel):
    tool_name: str
    status: str | None = None
    duration_ms: int | None = None
    external_cost: str | None = None


class ChatRunUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    quota_consumed: int | None = None
    tool_usage: list[ChatRunToolUsage] | None = None
    calls: list[ChatRunCall] | None = None


class ChatRunDetailResponse(BaseModel):
    run_id: str
    conversation_id: str
    mode: str | None = None
    model: str | None = None
    status: Literal["running", "completed", "failed", "cancelled", "unknown"]
    status_reason: str | None = None
    status_source: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    usage: ChatRunUsage = Field(default_factory=ChatRunUsage)
