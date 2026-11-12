"""模型调用明细、工具/技能资源、额度流水与价格版本。

Revision ID: d5a2c8e14b70
Revises: c3f1a9b27e84
Create Date: 2026-11-11 14:40:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d5a2c8e14b70"
down_revision: Union[str, Sequence[str], None] = "c3f1a9b27e84"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_usage_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", sa.String(length=128), nullable=True),
        sa.Column("message_id", sa.String(length=128), nullable=True),
        sa.Column("task_id", sa.String(length=128), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("settlement_key", sa.String(length=160), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("runtime_mode", sa.String(length=16), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost", sa.Numeric(18, 6), nullable=True),
        sa.Column("actual_cost", sa.Numeric(18, 6), nullable=True),
        sa.Column("cache_discount_amount", sa.Numeric(18, 6), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("usage_source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_llm_usage_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "settlement_key", name="uq_llm_usage_settlement"),
        sa.CheckConstraint(
            "status IN ('completed', 'cancelled', 'failed', 'partial')",
            name="ck_llm_usage_status",
        ),
    )
    op.create_index("ix_llm_usage_user_conversation", "llm_usage_records", ["user_id", "conversation_id", "created_at"])

    op.create_table(
        "llm_usage_breakdowns",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("usage_record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("attribution_method", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.ForeignKeyConstraint(["usage_record_id"], ["llm_usage_records.id"], name="fk_llm_breakdown_usage_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("direction IN ('input', 'output', 'cache')", name="ck_llm_breakdown_direction"),
        sa.CheckConstraint(
            "attribution_method IN ('provider_reported', 'tokenizer_estimated', 'heuristic_estimated', 'unavailable')",
            name="ck_llm_breakdown_method",
        ),
    )
    op.create_index("ix_llm_usage_breakdowns_record", "llm_usage_breakdowns", ["usage_record_id"])

    op.create_table(
        "tool_usage_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("llm_usage_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("conversation_id", sa.String(length=128), nullable=True),
        sa.Column("task_id", sa.String(length=128), nullable=True),
        sa.Column("tool_call_id", sa.String(length=128), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("tool_version", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("external_requests", sa.Integer(), nullable=True),
        sa.Column("external_cost", sa.Numeric(18, 6), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_tool_usage_user_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["llm_usage_id"], ["llm_usage_records.id"], name="fk_tool_usage_llm_id", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "tool_call_id", name="uq_tool_usage_call"),
    )
    op.create_index("ix_tool_usage_user_created", "tool_usage_records", ["user_id", "created_at"])

    op.create_table(
        "skill_usage_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("llm_usage_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("skill_id", sa.String(length=64), nullable=True),
        sa.Column("skill_name", sa.String(length=64), nullable=True),
        sa.Column("skill_version", sa.String(length=32), nullable=True),
        sa.Column("skill_execution_id", sa.String(length=160), nullable=False),
        sa.Column("conversation_id", sa.String(length=128), nullable=True),
        sa.Column("task_id", sa.String(length=128), nullable=True),
        sa.Column("instruction_tokens", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("direct_cost", sa.Numeric(18, 6), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_skill_usage_user_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["llm_usage_id"], ["llm_usage_records.id"], name="fk_skill_usage_llm_id", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "skill_execution_id", name="uq_skill_usage_execution"),
    )
    op.create_index("ix_skill_usage_user_created", "skill_usage_records", ["user_id", "created_at"])

    op.create_table(
        "quota_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("llm_usage_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("settlement_key", sa.String(length=160), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("amount_money", sa.Numeric(18, 6), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_quota_tx_user_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["llm_usage_id"], ["llm_usage_records.id"], name="fk_quota_tx_llm_id", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "settlement_key", "action", name="uq_quota_tx_settlement"),
        sa.CheckConstraint(
            "action IN ('reserve', 'consume', 'release', 'refund', 'adjustment')",
            name="ck_quota_tx_action",
        ),
    )
    op.create_index("ix_quota_tx_user_created", "quota_transactions", ["user_id", "created_at"])

    op.create_table(
        "model_pricing_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("input_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("output_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("reasoning_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("cache_read_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("cache_write_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("price_unit", sa.Integer(), server_default=sa.text("1000000"), nullable=False),
        sa.Column("currency", sa.String(length=8), server_default=sa.text("'USD'"), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_model_pricing_lookup", "model_pricing_versions", ["provider", "model", "effective_from"])


def downgrade() -> None:
    op.drop_index("ix_model_pricing_lookup", table_name="model_pricing_versions")
    op.drop_table("model_pricing_versions")
    op.drop_index("ix_quota_tx_user_created", table_name="quota_transactions")
    op.drop_table("quota_transactions")
    op.drop_index("ix_skill_usage_user_created", table_name="skill_usage_records")
    op.drop_table("skill_usage_records")
    op.drop_index("ix_tool_usage_user_created", table_name="tool_usage_records")
    op.drop_table("tool_usage_records")
    op.drop_index("ix_llm_usage_breakdowns_record", table_name="llm_usage_breakdowns")
    op.drop_table("llm_usage_breakdowns")
    op.drop_index("ix_llm_usage_user_conversation", table_name="llm_usage_records")
    op.drop_table("llm_usage_records")
