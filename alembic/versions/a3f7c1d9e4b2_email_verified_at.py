"""邮箱验证码登录：记录邮箱归属验证时间。

Revision ID: a3f7c1d9e4b2
Revises: c7d2e8f4a916
Create Date: 2026-11-22 11:50:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3f7c1d9e4b2"
down_revision: Union[str, Sequence[str], None] = "c7d2e8f4a916"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 存量账号保持 NULL：他们只用密码登录过，从未证明过邮箱归属。
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "email_verified_at")
