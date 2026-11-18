"""community connector auth headers

Revision ID: 9f2a7e1d3c45
Revises: b7d5e1c8a2f4
Create Date: 2026-11-17 22:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "9f2a7e1d3c45"
down_revision: Union[str, Sequence[str], None] = "b7d5e1c8a2f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "community_connectors",
        sa.Column("auth_headers", sa.String(length=255), server_default=sa.text("''"), nullable=False),
    )
    op.add_column(
        "community_connectors",
        sa.Column("allow_write", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("mcp_connections", sa.Column("last_error", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("mcp_connections", "last_error")
    op.drop_column("community_connectors", "allow_write")
    op.drop_column("community_connectors", "auth_headers")
