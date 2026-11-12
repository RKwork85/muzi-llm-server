"""额度消耗分析的只读响应。不参与扣减。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TokenFigure(BaseModel):
    value: int | None = None
    quality: Literal["actual", "estimated", "unavailable"]


class BreakdownItem(BaseModel):
    category: str
    label: str
    direction: Literal["input", "output", "cache"]
    figure: TokenFigure
    share: float | None = None


class CallGroup(BaseModel):
    role: Literal["primary", "summary", "other"]
    label: str
    count: int
    input_tokens: TokenFigure
    output_tokens: TokenFigure
    total_tokens: TokenFigure


class TimelineNode(BaseModel):
    id: str
    kind: str
    title: str
    name: str | None = None
    started_at: datetime | None = None
    duration_ms: int | None = None
    tokens: TokenFigure | None = None
    status: str
    event_id_masked: str | None = None
    detail: str | None = None


class TechnicalDetail(BaseModel):
    turn_id_masked: str
    execution_ids_masked: list[str] = Field(default_factory=list)
    request_ids_masked: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    raw_usage: list[dict] = Field(default_factory=list)
    billing_events: list[dict] = Field(default_factory=list)
    usage_source: str
    attribution_methods: list[str] = Field(default_factory=list)
    pricing_version: str
    summary_link: str | None = None


class UsageTurn(BaseModel):
    id: str
    kind: Literal["chat", "image"]
    mode: str | None = None
    model: str | None = None
    created_at: datetime
    prompt: str | None = None
    prompt_quality: Literal["actual", "unavailable"]
    conversation_title: str | None = None
    billed_tokens: TokenFigure
    input_tokens: TokenFigure
    output_tokens: TokenFigure
    cache_read_tokens: TokenFigure
    cache_write_tokens: TokenFigure
    call_count: int = 0
    summary_count: int = 0
    tool_count: int = 0
    image_count: int | None = None
    result: str
    link: str | None = None
    call_groups: list[CallGroup] = Field(default_factory=list)
    input_breakdown: list[BreakdownItem] = Field(default_factory=list)
    output_breakdown: list[BreakdownItem] = Field(default_factory=list)
    cache: list[BreakdownItem] = Field(default_factory=list)
    timeline: list[TimelineNode] = Field(default_factory=list)
    technical: TechnicalDetail


class UsageSummary(BaseModel):
    total: int = 0
    chat: int = 0
    image: int = 0
    count: int = 0


class Composition(BaseModel):
    cache_split: bool = False
    fresh_input: TokenFigure
    cache_read: TokenFigure
    output: TokenFigure
    total: TokenFigure
    billed_total: int = 0
    note: str | None = None


class TrendPoint(BaseModel):
    label: str
    chat: int = 0
    image: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class UsageAnalysisResponse(BaseModel):
    summary: UsageSummary
    composition: Composition
    trend_day: list[TrendPoint] = Field(default_factory=list)
    trend_round: list[TrendPoint] = Field(default_factory=list)
    trend_round_limited: bool = False
    models: list[str] = Field(default_factory=list)
    items: list[UsageTurn] = Field(default_factory=list)
    total: int = 0
    limit: int = 20
    offset: int = 0
    truncated: bool = False
    gaps: list[str] = Field(default_factory=list)
    range_key: str = "30d"
