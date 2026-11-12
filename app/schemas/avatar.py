"""头像上传与频次限制的请求与响应。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# 头像与显示名 30 天内最多修改 10 次。
AVATAR_CHANGE_LIMIT = 10
AVATAR_CHANGE_WINDOW_DAYS = 30
AVATAR_MAX_BYTES = 5 * 1024 * 1024
AVATAR_MIN_SIDE = 200


class AvatarUpload(BaseModel):
    """裁剪后的头像，以 data URL 形式提交（前端已生成方形裁剪结果）。"""

    model_config = ConfigDict(extra="forbid")

    data_url: str = Field(min_length=32, max_length=8 * 1024 * 1024)


class AvatarStatusOut(BaseModel):
    """当前头像与剩余修改次数。"""

    avatar: str | None = None
    remaining: int = AVATAR_CHANGE_LIMIT
    limit: int = AVATAR_CHANGE_LIMIT
    window_days: int = AVATAR_CHANGE_WINDOW_DAYS
