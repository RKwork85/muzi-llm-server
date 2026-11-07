"""数据处理工具使用的任务与存储上下文。不进入模型消息。"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.data.oss_client import OssUploadClient
    from app.data.store import DataStore

CURRENT_TASK_ID: ContextVar[str | None] = ContextVar("agent_data_task", default=None)
CURRENT_DATA_STORE: ContextVar["DataStore | None"] = ContextVar("agent_data_store", default=None)
CURRENT_OSS_CLIENT: ContextVar["OssUploadClient | None"] = ContextVar("agent_oss_client", default=None)
