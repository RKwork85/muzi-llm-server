"""技能自定义显示名与图标的请求与响应。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

# 技能图标远小于头像，限制 1MB，前端统一输出 256x256 方形图。
SKILL_ICON_MAX_BYTES = 1024 * 1024
SKILL_DISPLAY_NAME_MAX = 120


class SkillIconUpload(BaseModel):
    """自定义图标，以 data URL 提交（前端已生成方形结果）。"""

    model_config = ConfigDict(extra="forbid")

    data_url: str = Field(min_length=32, max_length=2 * 1024 * 1024)


class SkillDisplayNameUpdate(BaseModel):
    """重命名技能显示名。"""

    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=SKILL_DISPLAY_NAME_MAX)


class SkillCustomizationOut(BaseModel):
    """技能当前的显示名与图标。字段为空表示未自定义（回落到默认标识/名称）。"""

    skill_id: str
    display_name: str | None = None
    icon: str | None = None
