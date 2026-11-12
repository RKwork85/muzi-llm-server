"""history millisecond timestamps use bigint

Revision ID: e9c1b7a4d2f0
Revises: a7f3d1c9b2e4
Create Date: 2026-11-11 10:30:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e9c1b7a4d2f0"
down_revision: Union[str, Sequence[str], None] = "a7f3d1c9b2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # JS Date.now() 毫秒时间戳超过 int32，历史保存会 500。
    op.alter_column(
        "chat_history_sessions",
        "pinned_at",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )
    op.alter_column(
        "chat_history_messages",
        "thinking_started_at",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )
    op.alter_column(
        "chat_history_messages",
        "created_at_ms",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "chat_history_messages",
        "created_at_ms",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
    )
    op.alter_column(
        "chat_history_messages",
        "thinking_started_at",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
    )
    op.alter_column(
        "chat_history_sessions",
        "pinned_at",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
    )
