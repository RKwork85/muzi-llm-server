"""方舟文件归属。

Revision ID: c3f1a9b27e84
Revises: b4e8c1a09d27
Create Date: 2026-11-11 13:05:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3f1a9b27e84"
down_revision: Union[str, Sequence[str], None] = "b4e8c1a09d27"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ark_files",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("file_id", sa.String(length=128), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_ark_files_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("file_id", name="uq_ark_files_file_id"),
        comment="方舟文件归属。查询只允许上传者。",
    )
    op.create_index("ix_ark_files_user", "ark_files", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_ark_files_user", table_name="ark_files")
    op.drop_table("ark_files")
