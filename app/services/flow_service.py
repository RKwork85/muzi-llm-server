"""AI 画布文档：结构进数据库，data URL 进 muzi-oss。"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InvalidRequestError
from app.data.oss_client import OssUploadClient, OssUploadError
from app.models.flow import FLOW_DOCUMENT_KINDS, FlowDocument
from app.models.mixins import utcnow

logger = logging.getLogger("muzi_llm.flow")

_DOC_KEY = re.compile(r"^(apps|sessions|canvas):[A-Za-z0-9_-]+(?::[A-Za-z0-9_-]+){0,3}$")
_DATA_URL = re.compile(
    r"^data:(image/(?:png|jpeg|jpg|webp|gif));base64,([A-Za-z0-9+/=\r\n]+)$",
    re.IGNORECASE,
)
_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_MAX_PAYLOAD_BYTES = 1_500_000
_MAX_IMAGES = 16
_MAX_IMAGE_BYTES = 8 * 1024 * 1024


def parse_doc_key(doc_key: str) -> str:
    key = (doc_key or "").strip()
    if not _DOC_KEY.match(key):
        raise InvalidRequestError("画布文档 key 无效")
    kind = key.split(":", 1)[0]
    if kind not in FLOW_DOCUMENT_KINDS:
        raise InvalidRequestError("画布文档类型无效")
    return kind


def _scope_for(doc_key: str) -> str:
    tail = doc_key.rsplit(":", 1)[-1]
    return re.sub(r"[^A-Za-z0-9_-]", "", tail)[:64]


class FlowService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_items(self, user_id: Any) -> list[FlowDocument]:
        rows = await self._session.scalars(
            select(FlowDocument)
            .where(FlowDocument.user_id == user_id)
            .order_by(FlowDocument.updated_at.desc())
        )
        return list(rows)

    async def upsert(
        self,
        user_id: Any,
        doc_key: str,
        payload: dict[str, Any],
        oss: OssUploadClient | None,
    ) -> FlowDocument:
        kind = parse_doc_key(doc_key)
        if not isinstance(payload, dict):
            raise InvalidRequestError("画布文档必须是对象")
        stored = json.loads(json.dumps(payload))
        await externalize_data_urls(stored, oss, str(user_id), _scope_for(doc_key))
        encoded = json.dumps(stored, ensure_ascii=False).encode("utf-8")
        if len(encoded) > _MAX_PAYLOAD_BYTES:
            raise InvalidRequestError("画布文档过大")

        row = await self._session.scalar(
            select(FlowDocument).where(
                FlowDocument.user_id == user_id,
                FlowDocument.doc_key == doc_key,
            )
        )
        now = utcnow()
        if row is None:
            row = FlowDocument(
                user_id=user_id,
                doc_key=doc_key,
                kind=kind,
                payload=stored,
                updated_at=now,
            )
            self._session.add(row)
        else:
            row.kind = kind
            row.payload = stored
            row.updated_at = now
        await self._session.flush()
        return row

    async def delete(self, user_id: Any, doc_key: str) -> bool:
        parse_doc_key(doc_key)
        result = await self._session.execute(
            delete(FlowDocument).where(
                FlowDocument.user_id == user_id,
                FlowDocument.doc_key == doc_key,
            )
        )
        return bool(result.rowcount)


async def externalize_data_urls(
    node: Any,
    oss: OssUploadClient | None,
    user_id: str,
    scope: str,
    stats: dict[str, int] | None = None,
) -> None:
    """把快照里的 data URL 换成 OSS 公网地址。没有 OSS 时丢掉 data URL，避免撑爆数据库。"""
    counter = stats if stats is not None else {"images": 0}
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if isinstance(value, str) and value.startswith("data:image/"):
                node[key] = await _upload_data_url(value, oss, user_id, scope, counter)
            else:
                await externalize_data_urls(value, oss, user_id, scope, counter)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if isinstance(value, str) and value.startswith("data:image/"):
                node[index] = await _upload_data_url(value, oss, user_id, scope, counter)
            else:
                await externalize_data_urls(value, oss, user_id, scope, counter)


async def _upload_data_url(
    value: str,
    oss: OssUploadClient | None,
    user_id: str,
    scope: str,
    stats: dict[str, int],
) -> str:
    match = _DATA_URL.match(value.strip())
    if match is None:
        return ""
    if stats["images"] >= _MAX_IMAGES:
        raise InvalidRequestError("单份画布里的上传图过多")
    media = match.group(1).lower()
    if media == "image/jpg":
        media = "image/jpeg"
    try:
        content = base64.b64decode(re.sub(r"\s+", "", match.group(2)), validate=True)
    except (binascii.Error, ValueError):
        return ""
    if not content or len(content) > _MAX_IMAGE_BYTES:
        raise InvalidRequestError("上传图过大或为空")
    stats["images"] += 1
    if oss is None:
        logger.warning("flow image dropped because oss is not configured")
        return ""
    try:
        uploaded = await oss.upload_files(
            [(f"flow_{stats['images']}{_EXT.get(media, '.png')}", content)],
            user_id=user_id,
            category="image",
            scope=scope or None,
            content_types=[media],
        )
    except OssUploadError as exc:
        logger.warning("flow image upload failed error=%s", exc.code)
        return ""
    return uploaded[0] if uploaded else ""
