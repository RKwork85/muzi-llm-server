"""用户通用偏好设置。当前承载背景主题与背景图片，后续可扩展语言、外观等。"""

from __future__ import annotations

import base64
import binascii
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

_HEX_COLOR = r"^#[0-9a-fA-F]{6}$"
_DATA_URL = re.compile(r"^data:image/(png|jpeg|jpg|webp);base64,([A-Za-z0-9+/=\s]+)$")

DEFAULT_COLOR_CHAT = "#e0f7fa"
DEFAULT_COLOR_PRIMARY = "#ffffff"

# 背景图片以 data URL 形式存储，限制原始字节大小，避免 JSONB 无限膨胀。
BACKGROUND_IMAGE_MAX_BYTES = 5 * 1024 * 1024
BACKGROUND_MAX_BLUR = 40  # 毛玻璃模糊半径上限（像素）


class BackgroundSettings(BaseModel):
    """页面背景。由顶部色与底部色构成渐变；可选叠加一张背景图片并控制毛玻璃效果。"""

    model_config = ConfigDict(extra="forbid")

    color_chat: str = Field(default=DEFAULT_COLOR_CHAT, pattern=_HEX_COLOR)
    color_primary: str = Field(default=DEFAULT_COLOR_PRIMARY, pattern=_HEX_COLOR)
    image_url: str | None = Field(default=None, max_length=8 * 1024 * 1024)
    blur: float = Field(default=0, ge=0, le=BACKGROUND_MAX_BLUR)

    @field_validator("image_url")
    @classmethod
    def _validate_image_url(cls, value: str | None) -> str | None:
        """校验背景图片 data URL 的格式、大小。空值视为未设置。"""
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        match = _DATA_URL.fullmatch(value)
        if match is None:
            raise ValueError("背景图片格式不支持，仅支持 JPG、PNG、JPEG 或 WebP")
        try:
            raw = base64.b64decode(match.group(2), validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("背景图片数据无效") from None
        if len(raw) > BACKGROUND_IMAGE_MAX_BYTES:
            raise ValueError("背景图片大小不能超过 5MB")
        return value


class UserPreferencesOut(BaseModel):
    """当前用户的通用设置。"""

    model_config = ConfigDict(extra="forbid")

    background: BackgroundSettings = Field(default_factory=BackgroundSettings)


class UserPreferencesUpdate(BaseModel):
    """部分更新。字段为 None 表示不修改。"""

    model_config = ConfigDict(extra="forbid")

    background: BackgroundSettings | None = None
