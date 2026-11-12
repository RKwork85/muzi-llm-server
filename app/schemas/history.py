"""聊天历史（规范化）的请求/响应模型。

前端仍以整份会话对象（session + messages）传输，后端拆解落库为
chat_history_sessions + chat_history_messages 两张表，读取时重组返回。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HistorySaveRequest(ApiContract):
    title: str = Field(default="", max_length=200)
    snapshot: dict[str, Any] = Field(default_factory=dict)


class HistoryItem(ApiContract):
    chat_id: str
    title: str
    snapshot: dict[str, Any]
    updated_at: datetime


class HistoryListResponse(ApiContract):
    items: list[HistoryItem] = Field(default_factory=list)
