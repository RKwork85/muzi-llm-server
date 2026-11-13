"""Ask 运行身份与终态。

Revision ID: c5d8e2a14b70
Revises: b3e7a1c94d20
Create Date: 2026-11-13 12:55:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c5d8e2a14b70"
down_revision: Union[str, Sequence[str], None] = "b3e7a1c94d20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ask_runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("surface", sa.String(length=16), server_default=sa.text("'ask'"), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("external_id_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'running'"), nullable=False),
        sa.Column("status_reason", sa.String(length=64), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("surface = 'ask'", name="ck_ask_runs_surface"),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'cancelled', 'unknown')",
            name="ck_ask_runs_status",
        ),
        sa.CheckConstraint(
            "external_id_type IN ('client_run_id', 'request_id')",
            name="ck_ask_runs_external_type",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_ask_runs_owner_id", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["sessions.id"],
            name="fk_ask_runs_conversation_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ask_runs"),
        sa.UniqueConstraint("owner_id", "conversation_id", "external_id", name="uq_ask_runs_external"),
    )
    op.create_index("ix_ask_runs_owner_created", "ask_runs", ["owner_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_ask_runs_owner_created", table_name="ask_runs")
    op.drop_table("ask_runs")
