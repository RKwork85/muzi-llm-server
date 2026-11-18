"""plan todos (execution checklist)

Revision ID: b7d2e5a9c431
Revises: c9f1a7e2d5b3
Create Date: 2026-11-18 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "b7d2e5a9c431"
down_revision: Union[str, Sequence[str], None] = "c9f1a7e2d5b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "plan_todos",
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("todo_id", sa.String(length=128), nullable=False),
        sa.Column("content", sa.String(length=2000), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("depends_on", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_plan_revision", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], name="fk_plan_todos_plan_id", ondelete="CASCADE"),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed', 'cancelled')",
            name="ck_plan_todos_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "todo_id", name="uq_plan_todos_plan_todo"),
        comment="Execution checklist item; independent of plan revision.",
    )
    op.create_index("ix_plan_todos_plan_position", "plan_todos", ["plan_id", "position"])


def downgrade() -> None:
    op.drop_index("ix_plan_todos_plan_position", table_name="plan_todos")
    op.drop_table("plan_todos")
