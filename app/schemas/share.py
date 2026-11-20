"""公开分享对话的请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShareCreateRequest(ApiContract):
    chat_id: str = Field(default="", max_length=64)
    title: str = Field(default="", max_length=200)
    snapshot: dict[str, Any] = Field(default_factory=dict)


class ShareCreateResponse(ApiContract):
    share_id: str
    url: str


class ShareListItem(ApiContract):
    share_id: str
    title: str
    url: str
    source_name: str
    created_at: datetime


class ShareListResponse(ApiContract):
    items: list[ShareListItem] = Field(default_factory=list)


class SharePublicResponse(ApiContract):
    share_id: str
    title: str
    snapshot: dict[str, Any]
    source_name: str
    source_username: str | None = None
    source_avatar: str | None = None
    source_id: str | None = None
    created_at: datetime
