"""doc articles (muzidoc blog)

Revision ID: d4c9e7f2a1b3
Revises: a5f3c8d2e91b
Create Date: 2026-11-18 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d4c9e7f2a1b3"
down_revision: Union[str, Sequence[str], None] = "a5f3c8d2e91b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "doc_articles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), server_default=sa.text("''"), nullable=False),
        sa.Column("content", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('draft', 'published')", name="ck_doc_articles_status"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_doc_articles_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="用户文档/博客文章，markdown 内容，按用户隔离。",
    )
    op.create_index("ix_doc_articles_user_updated", "doc_articles", ["user_id", "updated_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_doc_articles_user_updated", table_name="doc_articles")
    op.drop_table("doc_articles")
