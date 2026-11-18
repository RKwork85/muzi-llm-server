"""内容创作任务表。PostgreSQL 是状态机真相；Redis 只做进度缓存与 SSE 广播。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.constants import (
    CONTENT_DELIVER_TYPES,
    CONTENT_TASK_STAGES,
    CONTENT_TASK_STATUSES,
    sql_in,
)
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

_JSON_OBJECT = text("'{}'::jsonb")
_JSON_ARRAY = text("'[]'::jsonb")


class ContentTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """内容创作任务：采集 → 工作流 → 发布。状态机真相落库。"""

    __tablename__ = "content_tasks"
    __table_args__ = (
        CheckConstraint(sql_in("status", CONTENT_TASK_STATUSES), name="ck_content_tasks_status"),
        CheckConstraint(sql_in("stage", CONTENT_TASK_STAGES), name="ck_content_tasks_stage"),
        CheckConstraint(
            f"(deliver_type IS NULL OR {sql_in('deliver_type', CONTENT_DELIVER_TYPES)})",
            name="ck_content_tasks_deliver_type",
        ),
        CheckConstraint("retry_count >= 0", name="ck_content_tasks_retry_count"),
        Index("ix_content_tasks_user_created", "user_id", "created_at"),
        Index("ix_content_tasks_stage_status", "stage", "status"),
        Index("ix_content_tasks_status", "status"),
        {"comment": "内容创作任务：采集 → 工作流 → 发布。状态机真相落库。"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_content_tasks_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="queued", server_default=text("'queued'")
    )
    stage: Mapped[str] = mapped_column(
        String(32), nullable=False, default="collect", server_default=text("'collect'")
    )
    # 交付链路（图文 / 笔记 / 视频）。采集阶段未定，提交任务节点时才确定。
    deliver_type: Mapped[str | None] = mapped_column(String(32))
    # 采集来源：[{platform, url}]。
    source_links: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=_JSON_ARRAY
    )
    # 工作流配置参数（style/length/tone/keywords/count/cover/seo...）。
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=_JSON_OBJECT
    )
    # 执行进度：{percent, current_step, total_steps, step_name, step_key, logs: [...]}。
    progress: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=_JSON_OBJECT
    )
    # 采集完成后的预览数据（DRAFT 时）。
    preview_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # 最终交付物结果（result_ref / asset_url 等）。
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
