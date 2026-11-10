"""用户通用偏好设置。当前只承载背景主题，后续可扩展语言、外观等。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

_HEX_COLOR = r"^#[0-9a-fA-F]{6}$"

DEFAULT_COLOR_CHAT = "#e0f7fa"
DEFAULT_COLOR_PRIMARY = "#ffffff"


class BackgroundSettings(BaseModel):
    """页面背景。由顶部色与底部色构成自上而下的渐变。"""

    model_config = ConfigDict(extra="forbid")

    color_chat: str = Field(default=DEFAULT_COLOR_CHAT, pattern=_HEX_COLOR)
    color_primary: str = Field(default=DEFAULT_COLOR_PRIMARY, pattern=_HEX_COLOR)


class UserPreferencesOut(BaseModel):
    """当前用户的通用设置。"""

    model_config = ConfigDict(extra="forbid")

    background: BackgroundSettings = Field(default_factory=BackgroundSettings)


class UserPreferencesUpdate(BaseModel):
    """部分更新。字段为 None 表示不修改。"""

    model_config = ConfigDict(extra="forbid")

    background: BackgroundSettings | None = None
