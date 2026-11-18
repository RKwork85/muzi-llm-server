"""内容创作任务 API 的请求/响应模型。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CollectLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: str = Field(min_length=1, max_length=64)
    url: str = Field(min_length=1, max_length=2048)


class CollectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    links: list[CollectLink] = Field(min_length=1, max_length=50)


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: uuid.UUID
    deliver_type: str = Field(pattern=r"^(image-text|note|video)$")
    config: dict[str, Any] = Field(default_factory=dict)


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    stage: str
    deliver_type: str | None
    source_links: list[dict[str, Any]]
    config: dict[str, Any]
    progress: dict[str, Any]
    preview_data: dict[str, Any] | None
    result: dict[str, Any] | None
    error: str | None
    retry_count: int
    queued_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CollectResponse(BaseModel):
    task_id: uuid.UUID
    status: str
    stage: str


class TaskListOut(BaseModel):
    items: list[TaskOut]
    total: int
    limit: int
    offset: int
