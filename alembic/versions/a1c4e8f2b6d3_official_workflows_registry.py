"""official workflows registry

新增 ``flow_official_workflows`` 表：官方工作流案例的平台级注册表。

- 不由 user_id 隔离：所有用户看到同一份官方案例；
- 超管（flow.manage）可创建 / 删除 / 调序 / 导入；
- 画布快照存 ``snapshot``（空画布 = nodes/edges 为空）；内置案例不落此表，
  画布仍由前端打包 demo JSON 提供。

Revision ID: a1c4e8f2b6d3
Revises: b2d8f4a1c703
Create Date: 2026-11-23 10:40:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1c4e8f2b6d3"
down_revision: Union[str, Sequence[str], None] = "b2d8f4a1c703"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "flow_official_workflows",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_key", sa.String(length=64), nullable=False),
        sa.Column("workflow_key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("icon", sa.String(length=16), server_default="✧", nullable=False),
        sa.Column("accent", sa.String(length=32), server_default="#6366f1", nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("tags", _JSON, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="ready", nullable=False),
        sa.Column("demo", sa.String(length=64), server_default="blank", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("snapshot", _JSON, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_key", "workflow_key", name="uq_flow_official_workflows_key"),
        comment="官方工作流案例（超管管理）：元信息 + 画布快照 + 排序。",
    )
    op.create_index(
        "ix_flow_official_workflows_ws_order",
        "flow_official_workflows",
        ["workspace_key", "sort_order"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_flow_official_workflows_ws_order", table_name="flow_official_workflows"
    )
    op.drop_table("flow_official_workflows")
