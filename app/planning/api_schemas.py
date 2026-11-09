"""Authenticated HTTP request and response schemas for planning persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.planning.contracts import Interaction, PlanSnapshot, PlanningEvent


class ApiContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationCreate(ApiContract):
    mode: Literal["ask", "plan", "agent"] = "ask"
    title: str = Field(default="", max_length=200)


class ConversationResponse(ApiContract):
    conversation_id: str
    mode: Literal["ask", "plan", "agent"]
    title: str
    created_at: datetime


class ConversationModeUpdate(ApiContract):
    expected_revision: int = Field(ge=0)
    mode: Literal["ask", "plan", "agent"]


class PlanCreate(ApiContract):
    conversation_id: str = Field(min_length=1, max_length=128)
    goal: str = Field(min_length=1, max_length=4000)


class PlanMessage(ApiContract):
    expected_revision: int = Field(ge=0)
    message: str = Field(min_length=1, max_length=8000)


class PlanRuntimeResponse(ApiContract):
    plan: PlanSnapshot
    interaction: Interaction | None = None
    response: str | None = None
    approved_revision: int | None = Field(default=None, ge=0)
    run: dict[str, object] | None = None
    # M3：交互动作的影响分析结果（受影响的决策键与步骤 id）。
    affected: dict[str, object] | None = None


class PlanEventList(ApiContract):
    events: list[PlanningEvent] = Field(default_factory=list)
    next_sequence: int = Field(ge=0)
