"""手机号登录：用户手机号与 phone 凭证。

Revision ID: f1a9c3e8b2d4
Revises: e7c4b2a91d58
Create Date: 2026-11-12 12:50:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1a9c3e8b2d4"
down_revision: Union[str, Sequence[str], None] = "e7c4b2a91d58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("phone", sa.String(length=20), nullable=True))
    op.create_unique_constraint("uq_users_phone", "users", ["phone"])
    op.create_check_constraint(
        "ck_users_phone",
        "users",
        "phone IS NULL OR phone ~ '^1[3-9][0-9]{9}$'",
    )
    op.drop_constraint("ck_user_credentials_provider", "user_credentials", type_="check")
    op.create_check_constraint(
        "ck_user_credentials_provider",
        "user_credentials",
        "provider IN ('email_password', 'github', 'phone')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_user_credentials_provider", "user_credentials", type_="check")
    op.create_check_constraint(
        "ck_user_credentials_provider",
        "user_credentials",
        "provider IN ('email_password', 'github')",
    )
    op.drop_constraint("ck_users_phone", "users", type_="check")
    op.drop_constraint("uq_users_phone", "users", type_="unique")
    op.drop_column("users", "phone")
