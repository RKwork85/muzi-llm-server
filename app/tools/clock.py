"""返回 UTC 时间，不读取系统文件或环境变量。"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


def current_time_handler(_: EmptyArgs) -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
