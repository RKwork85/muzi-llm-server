"""user avatar

Revision ID: d8f4a2c1e6b7
Revises: 1c5e2f8a9b3d
Create Date: 2026-11-10 14:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "d8f4a2c1e6b7"
down_revision: Union[str, Sequence[str], None] = "1c5e2f8a9b3d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """给 users 表增加头像字段，并新建头像/显示名修改记录表。"""
    op.add_column(
        "users",
        sa.Column("avatar", sa.Text(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("avatar_updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "profile_edits",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("field", sa.String(length=32), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("field IN ('avatar', 'display_name')", name="ck_profile_edits_field"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_profile_edits_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="头像与显示名修改记录，用于频次限制。",
    )
    op.create_index("ix_profile_edits_user_created", "profile_edits", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_profile_edits_user_created", table_name="profile_edits")
    op.drop_table("profile_edits")
    op.drop_column("users", "avatar_updated_at")
    op.drop_column("users", "avatar")
