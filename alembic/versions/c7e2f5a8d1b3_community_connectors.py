"""community connectors

Revision ID: c7e2f5a8d1b3
Revises: e1b7a4c9d2f3
Create Date: 2026-11-16 00:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c7e2f5a8d1b3"
down_revision: Union[str, Sequence[str], None] = "e1b7a4c9d2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "community_connectors",
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("qualified_name", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=200), server_default=sa.text("''"), nullable=False),
        sa.Column("transport", sa.String(length=16), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("icon_url", sa.String(length=255), nullable=True),
        sa.Column("website_url", sa.String(length=255), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_community_connectors_name"),
        sa.UniqueConstraint("qualified_name", name="uq_community_connectors_qualified"),
        sa.CheckConstraint("name ~ '^[a-z][a-z0-9_]{0,31}$'", name="ck_community_connectors_name"),
        sa.CheckConstraint("transport IN ('streamable_http', 'sse')", name="ck_community_connectors_transport"),
        comment="社区目录安装的远程 MCP 连接器。全局目录，连接仍按用户隔离。",
    )
    op.create_index("ix_community_connectors_qualified", "community_connectors", ["qualified_name"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_community_connectors_qualified", table_name="community_connectors")
    op.drop_table("community_connectors")
