"""mcp authorization grants

Revision ID: c4a8e2b71f03
Revises: b7e1c0a94d11
Create Date: 2026-11-08 15:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c4a8e2b71f03"
down_revision: Union[str, Sequence[str], None] = "b7e1c0a94d11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mcp_connector_grants",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("connector_id", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_mcp_connector_grants_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "connector_id", name="uq_mcp_connector_grants_user"),
        comment="用户对某个 MCP 连接器的授权。不等于工具授权。",
    )
    op.create_index("ix_mcp_connector_grants_user", "mcp_connector_grants", ["user_id"])
    op.create_table(
        "mcp_tool_grants",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("connector_id", sa.String(length=32), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_mcp_tool_grants_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "connector_id", "tool_name", name="uq_mcp_tool_grants_user_tool"),
        comment="用户对某个连接器上单个工具的授权。",
    )
    op.create_index("ix_mcp_tool_grants_user", "mcp_tool_grants", ["user_id", "connector_id"])
    op.create_table(
        "mcp_authorization_audits",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("connector_id", sa.String(length=32), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_mcp_authorization_audits_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="授权变更记录。不保存工具参数或令牌。",
    )
    op.create_index("ix_mcp_authorization_audits_user", "mcp_authorization_audits", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_mcp_authorization_audits_user", table_name="mcp_authorization_audits")
    op.drop_table("mcp_authorization_audits")
    op.drop_index("ix_mcp_tool_grants_user", table_name="mcp_tool_grants")
    op.drop_table("mcp_tool_grants")
    op.drop_index("ix_mcp_connector_grants_user", table_name="mcp_connector_grants")
    op.drop_table("mcp_connector_grants")
