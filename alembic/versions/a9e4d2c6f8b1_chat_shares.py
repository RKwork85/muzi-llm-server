"""chat shares

Revision ID: a9e4d2c6f8b1
Revises: c7e2f5a8d1b3
Create Date: 2026-11-16 19:20:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a9e4d2c6f8b1"
down_revision: Union[str, Sequence[str], None] = "c7e2f5a8d1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_shares",
        sa.Column("share_id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("chat_id", sa.String(length=64), server_default=sa.text("''"), nullable=False),
        sa.Column("title", sa.String(length=200), server_default=sa.text("''"), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("source_name", sa.String(length=120), server_default=sa.text("''"), nullable=False),
        sa.Column("source_username", sa.String(length=32), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_chat_shares_owner_id", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("share_id", name="uq_chat_shares_share_id"),
        comment="公开分享的对话快照，凭 share_id 只读访问。",
    )
    op.create_index("ix_chat_shares_owner_created", "chat_shares", ["owner_id", "created_at"])
    op.create_index("ix_chat_shares_share_id", "chat_shares", ["share_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_shares_share_id", table_name="chat_shares")
    op.drop_index("ix_chat_shares_owner_created", table_name="chat_shares")
    op.drop_table("chat_shares")
