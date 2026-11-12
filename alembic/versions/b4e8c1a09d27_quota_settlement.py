"""额度结算键、限额来源与大整数用量。

Revision ID: b4e8c1a09d27
Revises: e9c1b7a4d2f0
Create Date: 2026-11-11 12:50:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b4e8c1a09d27"
down_revision: Union[str, Sequence[str], None] = "e9c1b7a4d2f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "usage_quotas",
        sa.Column("limit_source", sa.String(length=16), server_default=sa.text("'manual'"), nullable=False),
    )
    op.execute(
        """
        UPDATE usage_quotas
        SET limit_source = CASE
            WHEN limit_value = 0 THEN 'unlimited'
            WHEN limit_value IN (100000, 300000, 500000) THEN 'role_default'
            ELSE 'manual'
        END
        """
    )
    op.alter_column(
        "usage_quotas",
        "limit_value",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )
    op.alter_column(
        "usage_quotas",
        "used_value",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )
    op.create_check_constraint(
        "ck_usage_quotas_limit_source",
        "usage_quotas",
        "limit_source IN ('role_default', 'manual', 'unlimited')",
    )

    op.add_column(
        "usage_records",
        sa.Column("source", sa.String(length=32), server_default=sa.text("'legacy'"), nullable=False),
    )
    op.add_column("usage_records", sa.Column("settlement_key", sa.String(length=160), nullable=True))
    op.add_column(
        "usage_records",
        sa.Column("result", sa.String(length=16), server_default=sa.text("'settled'"), nullable=False),
    )
    op.alter_column(
        "usage_records",
        "amount",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )
    op.create_check_constraint(
        "ck_usage_records_result",
        "usage_records",
        "result IN ('settled', 'exceeded', 'unsettled')",
    )
    op.create_index(
        "uq_usage_records_settlement",
        "usage_records",
        ["user_id", "source", "settlement_key"],
        unique=True,
        postgresql_where=sa.text("settlement_key IS NOT NULL"),
    )

    op.execute(
        """
        INSERT INTO usage_quotas (
            user_id, quota_key, period, limit_value, used_value, limit_source, id, created_at, updated_at
        )
        SELECT u.id, 'total_tokens', 'total',
               CASE WHEN sa.user_id IS NOT NULL THEN 0 ELSE 100000 END,
               0,
               CASE WHEN sa.user_id IS NOT NULL THEN 'unlimited' ELSE 'role_default' END,
               gen_random_uuid(), now(), now()
        FROM users u
        LEFT JOIN (
            SELECT ur.user_id
            FROM user_roles ur
            JOIN roles r ON r.id = ur.role_id
            WHERE r.code = 'super_admin'
        ) sa ON sa.user_id = u.id
        WHERE NOT EXISTS (
            SELECT 1 FROM usage_quotas q
            WHERE q.user_id = u.id AND q.quota_key = 'total_tokens' AND q.period = 'total'
        )
        """
    )
    op.execute(
        """
        UPDATE usage_quotas AS q
        SET used_value = GREATEST(q.used_value, COALESCE(s.total, 0))
        FROM (
            SELECT user_id, SUM(amount) AS total
            FROM usage_records
            GROUP BY user_id
        ) AS s
        WHERE q.user_id = s.user_id
          AND q.quota_key = 'total_tokens'
          AND q.period = 'total'
          AND q.limit_source = 'unlimited'
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM usage_records WHERE amount > 2147483647 OR amount < 0")
    op.drop_index("uq_usage_records_settlement", table_name="usage_records")
    op.drop_constraint("ck_usage_records_result", "usage_records", type_="check")
    op.alter_column(
        "usage_records",
        "amount",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
    op.drop_column("usage_records", "result")
    op.drop_column("usage_records", "settlement_key")
    op.drop_column("usage_records", "source")

    op.drop_constraint("ck_usage_quotas_limit_source", "usage_quotas", type_="check")
    op.execute("UPDATE usage_quotas SET used_value = LEAST(used_value, 2147483647), limit_value = LEAST(limit_value, 2147483647)")
    op.alter_column(
        "usage_quotas",
        "used_value",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
    op.alter_column(
        "usage_quotas",
        "limit_value",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
    op.drop_column("usage_quotas", "limit_source")
