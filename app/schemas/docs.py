"""muzidoc 文档/博客的请求与响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

DocStatus = Literal["draft", "published"]


class ApiContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocCreateRequest(ApiContract):
    title: str = Field(default="", max_length=200)
    content: str = Field(default="")
    status: DocStatus = "draft"


class DocUpdateRequest(ApiContract):
    title: str | None = Field(default=None, max_length=200)
    content: str | None = None
    status: DocStatus | None = None


class DocListItem(ApiContract):
    id: UUID
    title: str
    status: DocStatus
    excerpt: str
    created_at: datetime
    updated_at: datetime


class DocListResponse(ApiContract):
    items: list[DocListItem] = Field(default_factory=list)


class DocDetailResponse(ApiContract):
    id: UUID
    title: str
    content: str
    status: DocStatus
    created_at: datetime
    updated_at: datetime


class DocCreatedResponse(ApiContract):
    id: UUID
