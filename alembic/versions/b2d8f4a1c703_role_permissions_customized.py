"""角色权限是否被管理员手工配置过。

seed 在每次启动时按内置角色矩阵补齐缺失的权限（幂等、只增不减），
这会把管理员在后台刚取消掉的勾选在下次重启时又加回来。
新增 permissions_customized 标记后，seed 会跳过被管理员改过的角色，
让「出厂默认值」与「运行时配置」能区分开。

存量角色保持 false：他们还没被改过，seed 行为不变。

Revision ID: b2d8f4a1c703
Revises: a3f7c1d9e4b2
Create Date: 2026-11-22 12:30:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2d8f4a1c703"
down_revision: Union[str, Sequence[str], None] = "a3f7c1d9e4b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column(
            "permissions_customized",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("roles", "permissions_customized")
