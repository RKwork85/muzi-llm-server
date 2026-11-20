"""share source avatar

给 chat_shares 表增加分享者头像快照字段，公开分享页展示分享者身份。

Revision ID: c4b2e8d1f5a7
Revises: e4a7b1c9d2f5
Create Date: 2026-11-19 11:30:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c4b2e8d1f5a7"
down_revision: Union[str, Sequence[str], None] = "e4a7b1c9d2f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_shares",
        sa.Column("source_avatar", sa.Text(), nullable=True),
    )
    # 回填历史分享：从分享者账号把当前头像快照补进已存在的分享链接。
    op.execute(
        """
        UPDATE chat_shares AS cs
        SET source_avatar = u.avatar
        FROM users AS u
        WHERE cs.owner_id = u.id
          AND cs.source_avatar IS NULL
          AND u.avatar IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("chat_shares", "source_avatar")
