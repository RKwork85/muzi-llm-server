"""mcp user connections

Revision ID: b7e1c0a94d11
Revises: 6de1140597d2
Create Date: 2026-11-08 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "b7e1c0a94d11"
down_revision: Union[str, Sequence[str], None] = "6de1140597d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mcp_oauth_clients",
        sa.Column("connector_name", sa.String(length=32), nullable=False),
        sa.Column("client_id", sa.String(length=256), nullable=False),
        sa.Column("client_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connector_name", name="uq_mcp_oauth_clients_name"),
        comment="远程 MCP 动态注册得到的客户端。密钥只存密文。",
    )
    op.create_table(
        "mcp_connections",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("connector_name", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("selected_tools", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('connected', 'needs_reauth')", name="ck_mcp_connections_status"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_mcp_connections_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "connector_name", name="uq_mcp_connections_user_connector"),
        comment="用户与远程 MCP 的授权。令牌只存密文。",
    )
    op.create_index("ix_mcp_connections_user", "mcp_connections", ["user_id"], unique=False)
    op.create_table(
        "mcp_oauth_states",
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("connector_name", sa.String(length=32), nullable=False),
        sa.Column("code_verifier_encrypted", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("state_hash ~ '^[0-9a-f]{64}$'", name="ck_mcp_oauth_states_hash"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_mcp_oauth_states_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_hash", name="uq_mcp_oauth_states_hash"),
        comment="一次性 OAuth state。验证码只存密文。",
    )
    op.create_index("ix_mcp_oauth_states_expires", "mcp_oauth_states", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_mcp_oauth_states_expires", table_name="mcp_oauth_states")
    op.drop_table("mcp_oauth_states")
    op.drop_index("ix_mcp_connections_user", table_name="mcp_connections")
    op.drop_table("mcp_connections")
    op.drop_table("mcp_oauth_clients")
