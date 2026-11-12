"""通用响应结构。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class UsageBreakdown(BaseModel):
    direction: str
    category: str
    token_count: int | None = None
    attribution_method: str | None = None


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    breakdown: list[UsageBreakdown] | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadyResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    reason: str | None = None


class ModelCard(BaseModel):
    id: str
    provider: str


class ModelListResponse(BaseModel):
    data: list[ModelCard] = Field(default_factory=list)
