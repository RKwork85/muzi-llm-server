"""flow documents

AI 画布的应用列表、会话索引和画布快照。图片本身在 muzi-oss。

Revision ID: f6a1c8e4b2d9
Revises: c4b2e8d1f5a7
Create Date: 2026-11-22 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f6a1c8e4b2d9"
down_revision: Union[str, Sequence[str], None] = "c4b2e8d1f5a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "flow_documents",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("doc_key", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('apps', 'sessions', 'canvas')",
            name="ck_flow_documents_kind",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_flow_documents_user_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "doc_key", name="uq_flow_documents_user_key"),
        comment="AI 画布文档：应用列表、会话索引、画布快照。图片在 muzi-oss。",
    )
    op.create_index(
        "ix_flow_documents_user_updated",
        "flow_documents",
        ["user_id", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_flow_documents_user_updated", table_name="flow_documents")
    op.drop_table("flow_documents")
