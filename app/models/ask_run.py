"""Ask 运行元数据。只保存身份和状态，不保存提示词、回答或用量。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin

ASK_RUN_STATUSES = ("running", "completed", "failed", "cancelled", "unknown")
ASK_RUN_EXTERNAL_TYPES = ("client_run_id", "request_id")


class AskRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "ask_runs"
    __table_args__ = (
        CheckConstraint("surface = 'ask'", name="ck_ask_runs_surface"),
        CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'cancelled', 'unknown')",
            name="ck_ask_runs_status",
        ),
        CheckConstraint(
            "external_id_type IN ('client_run_id', 'request_id')",
            name="ck_ask_runs_external_type",
        ),
        UniqueConstraint(
            "owner_id",
            "conversation_id",
            "external_id",
            name="uq_ask_runs_external",
        ),
        Index("ix_ask_runs_owner_created", "owner_id", "created_at"),
        {"comment": "Ask 运行身份与终态。用量仍在原有计量表。"},
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_ask_runs_owner_id", ondelete="RESTRICT"),
        nullable=False,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sessions.id", name="fk_ask_runs_conversation_id", ondelete="CASCADE"),
        nullable=False,
    )
    surface: Mapped[str] = mapped_column(String(16), nullable=False, default="ask", server_default=text("'ask'"))
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    external_id_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running", server_default=text("'running'"))
    status_reason: Mapped[str | None] = mapped_column(String(64))
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
