"""quota adjustment reference for recharge idempotency

Revision ID: a5f3c8d2e91b
Revises: 9f2a7e1d3c45
Create Date: 2026-11-18 03:10:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a5f3c8d2e91b"
down_revision: Union[str, Sequence[str], None] = "9f2a7e1d3c45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("quota_adjustments", sa.Column("reference", sa.String(length=160), nullable=True))
    op.create_index(
        "uq_quota_adjustments_reference",
        "quota_adjustments",
        ["reference"],
        unique=True,
        postgresql_where=sa.text("reference IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_quota_adjustments_reference", table_name="quota_adjustments")
    op.drop_column("quota_adjustments", "reference")
