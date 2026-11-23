"""task quota config tables

Revision ID: e2a1b9c4d5f6
Revises: a1c4e8f2b6d3
Create Date: 2026-11-23 14:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2a1b9c4d5f6"
down_revision: Union[str, Sequence[str], None] = "a1c4e8f2b6d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_quota_configs",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("default_quota", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_type", name="uq_task_quota_configs_task_type"),
        sa.CheckConstraint("default_quota >= 0", name="ck_task_quota_configs_default_quota"),
        sa.CheckConstraint(
            "task_type ~ '^[a-z][a-z0-9_]{1,63}$'",
            name="ck_task_quota_configs_task_type",
        ),
        comment="任务类型额度配置。default_quota 为未匹配角色的兜底并发额度（0=不限）。",
    )

    op.create_table(
        "task_quota_role_limits",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("role_code", sa.String(length=64), nullable=False),
        sa.Column("quota_limit", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_type", "role_code", name="uq_task_quota_role_limits_type_role"),
        sa.CheckConstraint("quota_limit >= 0", name="ck_task_quota_role_limits_quota_limit"),
        sa.CheckConstraint(
            "role_code ~ '^[a-z][a-z0-9_]{1,63}$'",
            name="ck_task_quota_role_limits_role",
        ),
        comment="任务类型 × 角色的并发额度。quota_limit=0 表示不限。",
    )
    op.create_index(
        "ix_task_quota_role_limits_task_type", "task_quota_role_limits", ["task_type"]
    )

    op.create_table(
        "user_task_quota_overrides",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("quota_limit", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_task_quota_overrides_user_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id", "task_type", name="uq_user_task_quota_overrides_user_type"),
        sa.CheckConstraint("quota_limit >= 0", name="ck_user_task_quota_overrides_quota_limit"),
        comment="单用户任务类型并发额度覆盖。优先级高于角色额度。",
    )
    op.create_index(
        "ix_user_task_quota_overrides_user", "user_task_quota_overrides", ["user_id"]
    )

    # ---- seed 默认值（create_media 媒体创作）----
    configs = sa.table(
        "task_quota_configs",
        sa.column("task_type", sa.String()),
        sa.column("name", sa.String()),
        sa.column("enabled", sa.Boolean()),
        sa.column("default_quota", sa.Integer()),
    )
    op.bulk_insert(
        configs,
        [{"task_type": "create_media", "name": "媒体创作", "enabled": True, "default_quota": 3}],
    )

    role_limits = sa.table(
        "task_quota_role_limits",
        sa.column("task_type", sa.String()),
        sa.column("role_code", sa.String()),
        sa.column("quota_limit", sa.Integer()),
    )
    op.bulk_insert(
        role_limits,
        [
            {"task_type": "create_media", "role_code": "super_admin", "quota_limit": 0},
            {"task_type": "create_media", "role_code": "admin", "quota_limit": 4},
            {"task_type": "create_media", "role_code": "developer", "quota_limit": 3},
            {"task_type": "create_media", "role_code": "member", "quota_limit": 3},
            {"task_type": "create_media", "role_code": "guest", "quota_limit": 1},
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_user_task_quota_overrides_user", table_name="user_task_quota_overrides")
    op.drop_table("user_task_quota_overrides")
    op.drop_index("ix_task_quota_role_limits_task_type", table_name="task_quota_role_limits")
    op.drop_table("task_quota_role_limits")
    op.drop_table("task_quota_configs")
