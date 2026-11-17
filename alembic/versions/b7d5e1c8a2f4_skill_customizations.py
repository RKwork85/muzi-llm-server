"""skill customizations (rename + display name)

Revision ID: b7d5e1c8a2f4
Revises: e6f3c8b2a91d
Create Date: 2026-11-16 21:30:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b7d5e1c8a2f4"
down_revision: Union[str, Sequence[str], None] = "e6f3c8b2a91d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.rename_table("skill_icons", "skill_customizations")
    op.alter_column("skill_customizations", "icon", existing_type=sa.Text(), nullable=True)
    op.add_column("skill_customizations", sa.Column("display_name", sa.String(length=120), nullable=True))
    op.drop_index("ix_skill_icons_user", table_name="skill_customizations")
    op.create_index("ix_skill_customizations_user", "skill_customizations", ["user_id"])
    op.drop_constraint("uq_skill_icons_user_skill", "skill_customizations", type_="unique")
    op.create_unique_constraint(
        "uq_skill_customizations_user_skill", "skill_customizations", ["user_id", "skill_id"]
    )
    op.drop_constraint("fk_skill_icons_user_id", "skill_customizations", type_="foreignkey")
    op.create_foreign_key(
        "fk_skill_customizations_user_id",
        "skill_customizations",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_skill_customizations_user_id", "skill_customizations", type_="foreignkey")
    op.create_foreign_key(
        "fk_skill_icons_user_id",
        "skill_customizations",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("uq_skill_customizations_user_skill", "skill_customizations", type_="unique")
    op.create_unique_constraint(
        "uq_skill_icons_user_skill", "skill_customizations", ["user_id", "skill_id"]
    )
    op.drop_index("ix_skill_customizations_user", table_name="skill_customizations")
    op.create_index("ix_skill_icons_user", "skill_customizations", ["user_id"])
    op.drop_column("skill_customizations", "display_name")
    op.alter_column("skill_customizations", "icon", existing_type=sa.Text(), nullable=False)
    op.rename_table("skill_customizations", "skill_icons")
