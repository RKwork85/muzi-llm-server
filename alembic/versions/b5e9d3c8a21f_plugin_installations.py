"""plugin installations

Revision ID: b5e9d3c8a21f
Revises: a6c9e2f5b8d1
Create Date: 2026-11-15 23:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b5e9d3c8a21f"
down_revision: Union[str, Sequence[str], None] = "a6c9e2f5b8d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "plugin_installations",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("plugin_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('installed', 'uninstalled')", name="ck_plugin_installations_status"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_plugin_installations_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "plugin_id", name="uq_plugin_installations_user_plugin"),
        comment="用户对静态插件的显式状态。无记录时按目录默认值。MCP 连接器不在本表。",
    )
    op.create_index("ix_plugin_installations_user", "plugin_installations", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_plugin_installations_user", table_name="plugin_installations")
    op.drop_table("plugin_installations")
