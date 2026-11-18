"""content tasks

内容创作任务表：采集 → 工作流 → 发布。PostgreSQL 为状态机真相，Redis 只做进度缓存。

Revision ID: c9f1a7e2d5b3
Revises: b3c7d1e5a9f2
Create Date: 2026-11-18 21:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c9f1a7e2d5b3"
down_revision: Union[str, Sequence[str], None] = "b3c7d1e5a9f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "content_tasks",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("stage", sa.String(length=32), server_default=sa.text("'collect'"), nullable=False),
        sa.Column("deliver_type", sa.String(length=32), nullable=True),
        sa.Column(
            "source_links",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "progress",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("preview_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'draft', 'succeeded', 'failed', 'publishing', 'published', 'publish_failed', 'canceled')",
            name="ck_content_tasks_status",
        ),
        sa.CheckConstraint(
            "stage IN ('collect', 'workflow', 'publish')",
            name="ck_content_tasks_stage",
        ),
        sa.CheckConstraint(
            "deliver_type IS NULL OR deliver_type IN ('image-text', 'note', 'video')",
            name="ck_content_tasks_deliver_type",
        ),
        sa.CheckConstraint("retry_count >= 0", name="ck_content_tasks_retry_count"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_content_tasks_user_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="内容创作任务：采集 → 工作流 → 发布。状态机真相落库。",
    )
    op.create_index("ix_content_tasks_user_created", "content_tasks", ["user_id", "created_at"], unique=False)
    op.create_index("ix_content_tasks_stage_status", "content_tasks", ["stage", "status"], unique=False)
    op.create_index("ix_content_tasks_status", "content_tasks", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_content_tasks_status", table_name="content_tasks")
    op.drop_index("ix_content_tasks_stage_status", table_name="content_tasks")
    op.drop_index("ix_content_tasks_user_created", table_name="content_tasks")
    op.drop_table("content_tasks")
