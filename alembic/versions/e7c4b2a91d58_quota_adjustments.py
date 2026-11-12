"""额度显示值与调整流水。

Revision ID: e7c4b2a91d58
Revises: d5a2c8e14b70
Create Date: 2026-11-11 19:40:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7c4b2a91d58"
down_revision: Union[str, Sequence[str], None] = "d5a2c8e14b70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "usage_quotas",
        sa.Column("display_value", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "usage_quotas",
        sa.Column("adjust_reason", sa.String(length=200), server_default=sa.text("''"), nullable=False),
    )
    op.add_column("usage_quotas", sa.Column("adjusted_by", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("usage_quotas", sa.Column("adjusted_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE usage_quotas SET display_value = limit_value")
    op.create_foreign_key(
        "fk_usage_quotas_adjusted_by",
        "usage_quotas",
        "users",
        ["adjusted_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint("ck_usage_quotas_display_non_negative", "usage_quotas", "display_value >= 0")
    op.create_table(
        "quota_adjustments",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("quota_key", sa.String(length=64), nullable=False),
        sa.Column("period", sa.String(length=32), server_default=sa.text("'total'"), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("unit_size", sa.Integer(), server_default=sa.text("100000"), nullable=False),
        sa.Column("before_limit", sa.BigInteger(), nullable=False),
        sa.Column("after_limit", sa.BigInteger(), nullable=False),
        sa.Column("before_used", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.CheckConstraint(
            "action IN ('increase', 'decrease', 'reset')",
            name="ck_quota_adjustments_action",
        ),
        sa.CheckConstraint(
            "units >= 0 AND unit_size > 0 AND after_limit >= 0",
            name="ck_quota_adjustments_amounts",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_quota_adjustments_user_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], name="fk_quota_adjustments_actor_id", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quota_adjustments_user_created", "quota_adjustments", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_quota_adjustments_user_created", table_name="quota_adjustments")
    op.drop_table("quota_adjustments")
    op.drop_constraint("ck_usage_quotas_display_non_negative", "usage_quotas", type_="check")
    op.drop_constraint("fk_usage_quotas_adjusted_by", "usage_quotas", type_="foreignkey")
    op.drop_column("usage_quotas", "adjusted_at")
    op.drop_column("usage_quotas", "adjusted_by")
    op.drop_column("usage_quotas", "adjust_reason")
    op.drop_column("usage_quotas", "display_value")
