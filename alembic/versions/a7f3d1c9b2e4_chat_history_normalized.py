"""chat history normalized tables

Revision ID: a7f3d1c9b2e4
Revises: d8f4a2c1e6b7
Create Date: 2026-11-11 10:10:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a7f3d1c9b2e4"
down_revision: Union[str, Sequence[str], None] = "d8f4a2c1e6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_history_sessions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.String(length=64), nullable=False),
        sa.Column("chat_title", sa.String(length=200), server_default=sa.text("''"), nullable=False),
        sa.Column("chat_time", sa.String(length=64), nullable=True),
        sa.Column("mode", sa.String(length=32), nullable=True),
        sa.Column("model_id", sa.String(length=128), nullable=True),
        sa.Column("model_name", sa.String(length=128), nullable=True),
        sa.Column("provider_id", sa.String(length=64), nullable=True),
        sa.Column("model_category", sa.String(length=64), nullable=True),
        sa.Column("model_api", sa.String(length=64), nullable=True),
        sa.Column("pinned", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("pinned_at", sa.BigInteger(), nullable=True),
        sa.Column("server_conversation_id", sa.String(length=64), nullable=True),
        sa.Column("chat_summary", sa.Text(), nullable=True),
        sa.Column("chat_keywords", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_chat_history_sessions_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "chat_id", name="uq_chat_history_sessions_user_chat"),
        comment="用户聊天历史会话，按 chat_id 幂等 upsert。",
    )
    op.create_index("ix_chat_history_sessions_user_updated", "chat_history_sessions", ["user_id", "updated_at"])

    op.create_table(
        "chat_history_messages",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("reasoning_content", sa.Text(), nullable=True),
        sa.Column("thinking_duration_sec", sa.Float(), nullable=True),
        sa.Column("thinking_started_at", sa.BigInteger(), nullable=True),
        sa.Column("usage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("generated_images", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("downloads", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("tool_runs", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("search_results", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("search_query", sa.Text(), nullable=True),
        sa.Column("search_error", sa.Text(), nullable=True),
        sa.Column("trace", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("planning", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["chat_history_sessions.id"], name="fk_chat_history_messages_session_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("seq >= 0", name="ck_chat_history_messages_seq"),
        comment="用户聊天历史消息，追加写入，按 seq 排序。",
    )
    op.create_index("ix_chat_history_messages_session_seq", "chat_history_messages", ["session_id", "seq"])


def downgrade() -> None:
    op.drop_index("ix_chat_history_messages_session_seq", table_name="chat_history_messages")
    op.drop_table("chat_history_messages")
    op.drop_index("ix_chat_history_sessions_user_updated", table_name="chat_history_sessions")
    op.drop_table("chat_history_sessions")
