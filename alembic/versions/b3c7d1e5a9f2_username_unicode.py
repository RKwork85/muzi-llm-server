"""username supports unicode (chinese)

放宽 users.ck_users_username_normalized：原约束 `^[a-z][a-z0-9_]{2,31}$`
只允许 ASCII 小写，无法输入中文用户名。新约束只保留与 locale 无关的结构性
约束（小写归一 + 长度 2-32 + 无空白/控制字符），字符集由服务层
normalize_username 校验。

Revision ID: b3c7d1e5a9f2
Revises: d4c9e7f2a1b3
Create Date: 2026-11-18 16:40:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = "b3c7d1e5a9f2"
down_revision: Union[str, Sequence[str], None] = "d4c9e7f2a1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_CHECK = "username ~ '^[a-z][a-z0-9_]{2,31}$'"
NEW_CHECK = (
    "username = lower(username)"
    " AND char_length(username) BETWEEN 2 AND 32"
    " AND username !~ '[[:space:][:cntrl:]]'"
)


def upgrade() -> None:
    op.drop_constraint("ck_users_username_normalized", "users", type_="check")
    op.create_check_constraint("ck_users_username_normalized", "users", NEW_CHECK)


def downgrade() -> None:
    op.drop_constraint("ck_users_username_normalized", "users", type_="check")
    op.create_check_constraint("ck_users_username_normalized", "users", OLD_CHECK)
