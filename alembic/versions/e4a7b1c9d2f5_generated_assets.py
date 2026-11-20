"""generated assets

生成资产表：ComfyUI 等生成链路的产物落库，供资产页展示。

Revision ID: e4a7b1c9d2f5
Revises: b7d2e5a9c431
Create Date: 2026-11-19 00:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4a7b1c9d2f5"
down_revision: Union[str, Sequence[str], None] = "b7d2e5a9c431"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "generated_assets",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("workflow_id", sa.String(length=128), nullable=True),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("filename", sa.String(length=512), nullable=True),
        sa.Column("subfolder", sa.String(length=256), nullable=True),
        sa.Column("type", sa.String(length=64), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "source IN ('comfy', 'ark')",
            name="ck_generated_assets_source",
        ),
        sa.CheckConstraint(
            "kind IN ('image', 'video')",
            name="ck_generated_assets_kind",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_generated_assets_user_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "filename", name="uq_generated_assets_job_filename"),
        comment="生成资产：ComfyUI 等生成链路的产物，供资产页展示。",
    )
    op.create_index("ix_generated_assets_user_created", "generated_assets", ["user_id", "created_at"], unique=False)
    op.create_index("ix_generated_assets_user_kind", "generated_assets", ["user_id", "kind"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_generated_assets_user_kind", table_name="generated_assets")
    op.drop_index("ix_generated_assets_user_created", table_name="generated_assets")
    op.drop_table("generated_assets")
