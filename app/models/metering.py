"""模型调用、工具、技能与价格版本。额度扣减仍以 usage_records 为准。"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin

_JSON_OBJECT = text("'{}'::jsonb")


class LlmUsageRecord(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "llm_usage_records"
    __table_args__ = (
        UniqueConstraint("user_id", "settlement_key", name="uq_llm_usage_settlement"),
        Index("ix_llm_usage_user_conversation", "user_id", "conversation_id", "created_at"),
        CheckConstraint(
            "status IN ('completed', 'cancelled', 'failed', 'partial')",
            name="ck_llm_usage_status",
        ),
        {"comment": "一次实际模型请求。Token 权威值来自 Provider，分类在 breakdown。"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_llm_usage_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[str | None] = mapped_column(String(128))
    message_id: Mapped[str | None] = mapped_column(String(128))
    task_id: Mapped[str | None] = mapped_column(String(128))
    request_id: Mapped[str | None] = mapped_column(String(64))
    settlement_key: Mapped[str] = mapped_column(String(160), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    runtime_mode: Mapped[str | None] = mapped_column(String(16))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_write_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    actual_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    cache_discount_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    currency: Mapped[str | None] = mapped_column(String(8))
    usage_source: Mapped[str] = mapped_column(String(32), nullable=False, default="chat")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="completed")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LlmUsageBreakdown(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "llm_usage_breakdowns"
    __table_args__ = (
        Index("ix_llm_usage_breakdowns_record", "usage_record_id"),
        CheckConstraint("direction IN ('input', 'output', 'cache')", name="ck_llm_breakdown_direction"),
        CheckConstraint(
            "attribution_method IN ('provider_reported', 'tokenizer_estimated', 'heuristic_estimated', 'unavailable')",
            name="ck_llm_breakdown_method",
        ),
        {"comment": "一次模型调用的分类构成。未提供的分类不写 0。"},
    )

    usage_record_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("llm_usage_records.id", name="fk_llm_breakdown_usage_id", ondelete="CASCADE"),
        nullable=False,
    )
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer)
    attribution_method: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=_JSON_OBJECT,
    )


class ToolUsageRecord(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "tool_usage_records"
    __table_args__ = (
        UniqueConstraint("user_id", "tool_call_id", name="uq_tool_usage_call"),
        Index("ix_tool_usage_user_created", "user_id", "created_at"),
        {"comment": "工具执行本身的资源。外部费用不是 LLM Token。"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_tool_usage_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    llm_usage_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("llm_usage_records.id", name="fk_tool_usage_llm_id", ondelete="SET NULL"),
    )
    conversation_id: Mapped[str | None] = mapped_column(String(128))
    task_id: Mapped[str | None] = mapped_column(String(128))
    tool_call_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_version: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="completed")
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    external_requests: Mapped[int | None] = mapped_column(Integer)
    external_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    currency: Mapped[str | None] = mapped_column(String(8))
    error: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))


class SkillUsageRecord(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "skill_usage_records"
    __table_args__ = (
        UniqueConstraint("user_id", "skill_execution_id", name="uq_skill_usage_execution"),
        Index("ix_skill_usage_user_created", "user_id", "created_at"),
        {"comment": "Skill 注入与执行。指令 Token 已计入模型输入，这里不另扣额度。"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_skill_usage_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    llm_usage_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("llm_usage_records.id", name="fk_skill_usage_llm_id", ondelete="SET NULL"),
    )
    skill_id: Mapped[str | None] = mapped_column(String(64))
    skill_name: Mapped[str | None] = mapped_column(String(64))
    skill_version: Mapped[str | None] = mapped_column(String(32))
    skill_execution_id: Mapped[str] = mapped_column(String(160), nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(String(128))
    task_id: Mapped[str | None] = mapped_column(String(128))
    instruction_tokens: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="completed")
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    direct_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    currency: Mapped[str | None] = mapped_column(String(8))


class QuotaTransaction(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "quota_transactions"
    __table_args__ = (
        UniqueConstraint("user_id", "settlement_key", "action", name="uq_quota_tx_settlement"),
        Index("ix_quota_tx_user_created", "user_id", "created_at"),
        CheckConstraint(
            "action IN ('reserve', 'consume', 'release', 'refund', 'adjustment')",
            name="ck_quota_tx_action",
        ),
        {"comment": "额度流水。与 Token 分类、外部费用分开。"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_quota_tx_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    llm_usage_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("llm_usage_records.id", name="fk_quota_tx_llm_id", ondelete="SET NULL"),
    )
    settlement_key: Mapped[str] = mapped_column(String(160), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    amount_money: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    currency: Mapped[str | None] = mapped_column(String(8))


class ModelPricingVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "model_pricing_versions"
    __table_args__ = (
        Index("ix_model_pricing_lookup", "provider", "model", "effective_from"),
        {"comment": "模型价格版本。实际费用使用请求当时生效的快照。"},
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    input_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    output_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    reasoning_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    cache_read_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    cache_write_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    price_unit: Mapped[int] = mapped_column(Integer, nullable=False, default=1000000, server_default=text("1000000"))
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD", server_default=text("'USD'"))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SummaryUpdate(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """一次上下文摘要更新。只追加，不覆盖上一次的正文和用量。"""

    __tablename__ = "summary_updates"
    __table_args__ = (
        UniqueConstraint("user_id", "settlement_key", name="uq_summary_updates_settlement"),
        Index("ix_summary_updates_user_conversation", "user_id", "conversation_id", "called_at"),
        {"comment": "上下文摘要的每一次更新。Token 来自当次模型调用，不另计一笔额度。"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_summary_updates_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[str | None] = mapped_column(String(128))
    settlement_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="deepseek")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    summary_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    called_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_write_tokens: Mapped[int | None] = mapped_column(Integer)
    input_breakdown: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    output_breakdown: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    cache_breakdown: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    tool_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
