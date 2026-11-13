"""给计量表加统一 trace 标识（trace_id / span_id / parent_span_id）。

Revision ID: a6c9e2f5b8d1
Revises: c5d8e2a14b70
Create Date: 2026-11-13 15:00:00.000000

历史数据 trace_id 保持为空，从新数据开始追溯。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a6c9e2f5b8d1"
down_revision: Union[str, Sequence[str], None] = "c5d8e2a14b70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # llm_usage_records：trace_id + span_id + parent_span_id
    op.add_column("llm_usage_records", sa.Column("trace_id", sa.String(length=64), nullable=True))
    op.add_column("llm_usage_records", sa.Column("span_id", sa.String(length=64), nullable=True))
    op.add_column("llm_usage_records", sa.Column("parent_span_id", sa.String(length=64), nullable=True))
    op.create_index("ix_llm_usage_trace", "llm_usage_records", ["trace_id"])

    # tool_usage_records
    op.add_column("tool_usage_records", sa.Column("trace_id", sa.String(length=64), nullable=True))
    op.add_column("tool_usage_records", sa.Column("span_id", sa.String(length=64), nullable=True))
    op.add_column("tool_usage_records", sa.Column("parent_span_id", sa.String(length=64), nullable=True))
    op.create_index("ix_tool_usage_trace", "tool_usage_records", ["trace_id"])

    # skill_usage_records
    op.add_column("skill_usage_records", sa.Column("trace_id", sa.String(length=64), nullable=True))
    op.add_column("skill_usage_records", sa.Column("span_id", sa.String(length=64), nullable=True))
    op.add_column("skill_usage_records", sa.Column("parent_span_id", sa.String(length=64), nullable=True))
    op.create_index("ix_skill_usage_trace", "skill_usage_records", ["trace_id"])

    # quota_transactions
    op.add_column("quota_transactions", sa.Column("trace_id", sa.String(length=64), nullable=True))
    op.create_index("ix_quota_tx_trace", "quota_transactions", ["trace_id"])

    # usage_records（rbac）
    op.add_column("usage_records", sa.Column("trace_id", sa.String(length=64), nullable=True))
    op.create_index("ix_usage_records_trace", "usage_records", ["trace_id"])


def downgrade() -> None:
    op.drop_index("ix_usage_records_trace", table_name="usage_records")
    op.drop_column("usage_records", "trace_id")

    op.drop_index("ix_quota_tx_trace", table_name="quota_transactions")
    op.drop_column("quota_transactions", "trace_id")

    op.drop_index("ix_skill_usage_trace", table_name="skill_usage_records")
    op.drop_column("skill_usage_records", "parent_span_id")
    op.drop_column("skill_usage_records", "span_id")
    op.drop_column("skill_usage_records", "trace_id")

    op.drop_index("ix_tool_usage_trace", table_name="tool_usage_records")
    op.drop_column("tool_usage_records", "parent_span_id")
    op.drop_column("tool_usage_records", "span_id")
    op.drop_column("tool_usage_records", "trace_id")

    op.drop_index("ix_llm_usage_trace", table_name="llm_usage_records")
    op.drop_column("llm_usage_records", "parent_span_id")
    op.drop_column("llm_usage_records", "span_id")
    op.drop_column("llm_usage_records", "trace_id")
