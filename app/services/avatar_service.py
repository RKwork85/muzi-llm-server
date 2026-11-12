"""头像校验、频次限制与落库。数据以 data URL 存于 users.avatar。"""

from __future__ import annotations

import base64
import binascii
import re
import struct
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AvatarLimitExceededError, InvalidRequestError
from app.models.mixins import utcnow
from app.models.tables import ProfileEdit, User
from app.schemas.avatar import (
    AVATAR_CHANGE_LIMIT,
    AVATAR_CHANGE_WINDOW_DAYS,
    AVATAR_MAX_BYTES,
    AVATAR_MIN_SIDE,
)

_DATA_URL = re.compile(r"^data:image/(png|jpeg|jpg|webp);base64,([A-Za-z0-9+/=\s]+)$")


class AvatarService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def remaining(self, user: User) -> int:
        """返回当前用户在 30 天窗口内还可修改头像的次数。"""
        used = await self._recent_edits(user.id, "avatar")
        return max(0, AVATAR_CHANGE_LIMIT - used)

    async def update_avatar(self, user: User, data_url: str) -> None:
        """校验并写入头像，记录一次修改。超过频次上限时抛错。"""
        remaining = await self.remaining(user)
        if remaining <= 0:
            raise AvatarLimitExceededError()
        self.validate_data_url(data_url)
        user.avatar = data_url
        user.avatar_updated_at = utcnow()
        self.session.add(
            ProfileEdit(user_id=user.id, field="avatar")
        )

    @staticmethod
    def validate_data_url(data_url: str) -> bytes:
        """校验 data URL 的格式、大小与图片尺寸，返回解码后的字节。"""
        match = _DATA_URL.fullmatch(data_url.strip())
        if match is None:
            raise InvalidRequestError("头像格式不支持，仅支持 JPG、PNG、JPEG 或 WebP")
        try:
            raw = base64.b64decode(match.group(2), validate=True)
        except (binascii.Error, ValueError):
            raise InvalidRequestError("头像数据无效") from None
        if len(raw) > AVATAR_MAX_BYTES:
            raise InvalidRequestError("头像大小不能超过 5MB")
        width, height = _image_dimensions(match.group(1), raw)
        if width is not None and min(width, height) < AVATAR_MIN_SIDE:
            raise InvalidRequestError("头像分辨率建议不低于 200x200px")
        return raw

    async def _recent_edits(self, user_id: object, field: str) -> int:
        since = utcnow() - timedelta(days=AVATAR_CHANGE_WINDOW_DAYS)
        count = await self.session.scalar(
            select(func.count(ProfileEdit.id)).where(
                ProfileEdit.user_id == user_id,
                ProfileEdit.field == field,
                ProfileEdit.created_at >= since,
            )
        )
        return int(count or 0)


def _image_dimensions(kind: str, raw: bytes) -> tuple[int, int] | None:
    """从图片头部解析宽高。解析失败返回 None，跳过尺寸校验。"""
    try:
        if kind == "png":
            if raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) >= 24:
                width, height = struct.unpack(">II", raw[16:24])
                return width, height
        if kind in {"jpeg", "jpg"}:
            if raw[:2] == b"\xff\xd8":
                offset = 2
                length = len(raw)
                while offset + 9 < length:
                    if raw[offset] != 0xFF:
                        offset += 1
                        continue
                    marker = raw[offset + 1]
                    # SOF0..SOF15（不含 SOF4/DHT 等）携带尺寸。
                    if marker in {
                        0xC0, 0xC1, 0xC2, 0xC3,
                        0xC5, 0xC6, 0xC7,
                        0xC9, 0xCA, 0xCB,
                        0xCD, 0xCE, 0xCF,
                    }:
                        height, width = struct.unpack(">HH", raw[offset + 5 : offset + 9])
                        return width, height
                    segment_len = struct.unpack(">H", raw[offset + 2 : offset + 4])[0]
                    offset += 2 + segment_len
    except (struct.error, IndexError):
        return None
    return None
