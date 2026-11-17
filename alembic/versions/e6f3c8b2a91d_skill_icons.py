"""skill icons

Revision ID: e6f3c8b2a91d
Revises: a9e4d2c6f8b1
Create Date: 2026-11-16 21:10:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e6f3c8b2a91d"
down_revision: Union[str, Sequence[str], None] = "a9e4d2c6f8b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "skill_icons",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.String(length=64), nullable=False),
        sa.Column("icon", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_skill_icons_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "skill_id", name="uq_skill_icons_user_skill"),
        comment="用户为已上传技能自定义的图标，data URL 存储。",
    )
    op.create_index("ix_skill_icons_user", "skill_icons", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_skill_icons_user", table_name="skill_icons")
    op.drop_table("skill_icons")
