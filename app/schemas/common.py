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


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


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
