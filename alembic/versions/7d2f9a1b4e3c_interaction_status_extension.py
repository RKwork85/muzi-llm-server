"""interaction status extension for action protocol

Revision ID: 7d2f9a1b4e3c
Revises: f38a1d2e9c70
Create Date: 2026-11-09 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "7d2f9a1b4e3c"
down_revision: Union[str, Sequence[str], None] = "f38a1d2e9c70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # M2：交互动作协议引入 processing / completed / failed 状态。
    op.drop_constraint("ck_plan_interactions_status", "plan_interactions", type_="check")
    op.create_check_constraint(
        "ck_plan_interactions_status",
        "plan_interactions",
        "status IN ('pending', 'submitted', 'processing', 'completed', 'failed', 'skipped', 'expired', 'superseded', 'cancelled')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_plan_interactions_status", "plan_interactions", type_="check")
    op.create_check_constraint(
        "ck_plan_interactions_status",
        "plan_interactions",
        "status IN ('pending', 'submitted', 'skipped', 'expired', 'superseded', 'cancelled')",
    )
