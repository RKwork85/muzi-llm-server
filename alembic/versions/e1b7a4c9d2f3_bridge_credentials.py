"""bridge credentials

Revision ID: e1b7a4c9d2f3
Revises: b5e9d3c8a21f
Create Date: 2026-11-15 23:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e1b7a4c9d2f3"
down_revision: Union[str, Sequence[str], None] = "b5e9d3c8a21f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bridge_credentials",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("connector_name", sa.String(length=32), nullable=False),
        sa.Column("credentials_encrypted", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_bridge_credentials_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "connector_name", name="uq_bridge_credentials_user_connector"),
        comment="用户对自建 bridge 连接器的凭据（企业微信/飞书）。AES-GCM 密文，密钥来自环境。",
    )
    op.create_index("ix_bridge_credentials_user", "bridge_credentials", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_bridge_credentials_user", table_name="bridge_credentials")
    op.drop_table("bridge_credentials")
