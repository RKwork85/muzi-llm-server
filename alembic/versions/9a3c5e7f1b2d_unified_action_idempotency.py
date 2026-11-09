"""unified interaction action idempotency table

Revision ID: 9a3c5e7f1b2d
Revises: 7d2f9a1b4e3c
Create Date: 2026-11-09 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "9a3c5e7f1b2d"
down_revision: Union[str, Sequence[str], None] = "7d2f9a1b4e3c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "plan_actions",
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=True),
        sa.Column("interaction_id", sa.String(length=128), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("result_revision", sa.Integer(), nullable=False),
        sa.Column("result_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], name="fk_plan_actions_plan_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_plan_actions_owner_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "idempotency_key", name="uq_plan_actions_idempotency"),
        comment="Idempotent unified interaction action and its committed result snapshot.",
    )
    op.create_index("ix_plan_actions_owner_created", "plan_actions", ["owner_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_plan_actions_owner_created", table_name="plan_actions")
    op.drop_table("plan_actions")
