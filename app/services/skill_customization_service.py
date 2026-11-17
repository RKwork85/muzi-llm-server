"""技能自定义（显示名与图标）校验与落库。数据存于 skill_customizations。"""

from __future__ import annotations

import base64
import binascii
import re

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InvalidRequestError
from app.models.tables import SkillCustomization
from app.schemas.skill_customization import SKILL_ICON_MAX_BYTES

_DATA_URL = re.compile(r"^data:image/(png|jpeg|jpg|webp);base64,([A-Za-z0-9+/=\s]+)$")


class SkillCustomizationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def validate_data_url(data_url: str) -> bytes:
        """校验 data URL 的格式与大小，返回解码后的字节。"""
        match = _DATA_URL.fullmatch(data_url.strip())
        if match is None:
            raise InvalidRequestError("图标格式不支持，仅支持 PNG、JPEG、JPG 或 WebP")
        try:
            raw = base64.b64decode(match.group(2), validate=True)
        except (binascii.Error, ValueError):
            raise InvalidRequestError("图标数据无效") from None
        if len(raw) > SKILL_ICON_MAX_BYTES:
            raise InvalidRequestError("图标大小不能超过 1MB")
        return raw

    @staticmethod
    def validate_display_name(display_name: str) -> str:
        cleaned = display_name.strip()
        if not cleaned:
            raise InvalidRequestError("显示名不能为空")
        if len(cleaned) > 120:
            raise InvalidRequestError("显示名不能超过 120 个字符")
        return cleaned

    async def _row(self, user_id: object, skill_id: str) -> SkillCustomization | None:
        return await self.session.scalar(
            select(SkillCustomization).where(
                SkillCustomization.user_id == user_id,
                SkillCustomization.skill_id == skill_id,
            )
        )

    async def _get_or_create(self, user_id: object, skill_id: str) -> SkillCustomization:
        row = await self._row(user_id, skill_id)
        if row is None:
            row = SkillCustomization(user_id=user_id, skill_id=skill_id)
            self.session.add(row)
        return row

    async def set_icon(self, user_id: object, skill_id: str, data_url: str) -> str:
        self.validate_data_url(data_url)
        row = await self._get_or_create(user_id, skill_id)
        row.icon = data_url
        return data_url

    async def remove_icon(self, user_id: object, skill_id: str) -> bool:
        row = await self._row(user_id, skill_id)
        if row is None or row.icon is None:
            return False
        row.icon = None
        await self._prune_if_empty(row)
        return True

    async def set_display_name(self, user_id: object, skill_id: str, display_name: str) -> str:
        cleaned = self.validate_display_name(display_name)
        row = await self._get_or_create(user_id, skill_id)
        row.display_name = cleaned
        return cleaned

    async def remove_display_name(self, user_id: object, skill_id: str) -> bool:
        row = await self._row(user_id, skill_id)
        if row is None or row.display_name is None:
            return False
        row.display_name = None
        await self._prune_if_empty(row)
        return True

    async def _prune_if_empty(self, row: SkillCustomization) -> None:
        """显示名与图标都为空时删除整行，保持表干净。"""
        if row.icon is None and row.display_name is None:
            await self.session.delete(row)
