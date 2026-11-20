"""生成资产表：ComfyUI 等生成链路的产物落库，供资产页展示。

与对话里的 generated_images（存于 chat_history_messages）不同，这里沉淀的是
「AI 素材生成」板块（muzi-comfy-service）产出的图片/视频，独立成表、按 user_id
隔离，便于资产页统一展示与后续扩展（分享、删除、OSS 迁移等）。
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.constants import sql_in
from app.models.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin

GENERATED_ASSET_SOURCES = ("comfy", "ark")
GENERATED_ASSET_KINDS = ("image", "video")


class GeneratedAsset(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "generated_assets"
    __table_args__ = (
        # 幂等：同一个 job 的同一个文件只落一条（主服务查询接口反复轮询时不重复写入）。
        UniqueConstraint("job_id", "filename", name="uq_generated_assets_job_filename"),
        CheckConstraint(sql_in("source", GENERATED_ASSET_SOURCES), name="ck_generated_assets_source"),
        CheckConstraint(sql_in("kind", GENERATED_ASSET_KINDS), name="ck_generated_assets_kind"),
        Index("ix_generated_assets_user_created", "user_id", "created_at"),
        Index("ix_generated_assets_user_kind", "user_id", "kind"),
        {"comment": "生成资产：ComfyUI 等生成链路的产物，供资产页展示。"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_generated_assets_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="comfy")
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    workflow_id: Mapped[str | None] = mapped_column(String(128))
    job_id: Mapped[str | None] = mapped_column(String(64))
    prompt: Mapped[str | None] = mapped_column(Text)
    # ComfyUI 本地文件名三元组。未配 OSS 时靠它经 /files 接口拉取二进制。
    filename: Mapped[str | None] = mapped_column(String(512))
    subfolder: Mapped[str | None] = mapped_column(String(256))
    type: Mapped[str | None] = mapped_column(String(64))
    # OSS 公网地址（配了 OSS 才有）；为空时前端走 /files 代理取 blob。
    url: Mapped[str | None] = mapped_column(Text)
