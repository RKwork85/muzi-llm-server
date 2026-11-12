"""上下文摘要的每一次更新，并补上图片消耗所属会话。

Revision ID: a8d4e2c91b07
Revises: f1a9c3e8b2d4
Create Date: 2026-11-12 17:10:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a8d4e2c91b07"
down_revision: Union[str, Sequence[str], None] = "f1a9c3e8b2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "summary_updates",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", sa.String(length=128), nullable=True),
        sa.Column("settlement_key", sa.String(length=160), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("summary_text", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("called_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=True),
        sa.Column("input_breakdown", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("output_breakdown", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("cache_breakdown", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("tool_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("call_count", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_summary_updates_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "settlement_key", name="uq_summary_updates_settlement"),
    )
    op.create_index(
        "ix_summary_updates_user_conversation",
        "summary_updates",
        ["user_id", "conversation_id", "called_at"],
    )
    op.execute(
        """
        INSERT INTO summary_updates (
            user_id, conversation_id, settlement_key, request_id, provider, model,
            summary_text, called_at, input_tokens, output_tokens, total_tokens,
            cache_read_tokens, cache_write_tokens, input_breakdown, output_breakdown,
            cache_breakdown, tool_count, call_count
        )
        SELECT
            r.user_id,
            r.conversation_id,
            r.settlement_key,
            r.request_id,
            r.provider,
            r.model,
            '',
            COALESCE(r.completed_at, r.created_at),
            r.input_tokens,
            r.output_tokens,
            r.total_tokens,
            r.cache_read_tokens,
            r.cache_write_tokens,
            COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'direction', b.direction,
                    'category', b.category,
                    'token_count', b.token_count,
                    'attribution_method', b.attribution_method
                ))
                FROM llm_usage_breakdowns b
                WHERE b.usage_record_id = r.id
                  AND b.direction = 'input'
                  AND b.token_count > 0
                  AND b.attribution_method <> 'unavailable'
            ), '[]'::jsonb),
            COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'direction', b.direction,
                    'category', b.category,
                    'token_count', b.token_count,
                    'attribution_method', b.attribution_method
                ))
                FROM llm_usage_breakdowns b
                WHERE b.usage_record_id = r.id
                  AND b.direction = 'output'
                  AND b.token_count > 0
                  AND b.attribution_method <> 'unavailable'
            ), '[]'::jsonb),
            COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'direction', b.direction,
                    'category', b.category,
                    'token_count', b.token_count,
                    'attribution_method', b.attribution_method
                ))
                FROM llm_usage_breakdowns b
                WHERE b.usage_record_id = r.id
                  AND b.direction = 'cache'
                  AND b.token_count > 0
                  AND b.attribution_method <> 'unavailable'
            ), '[]'::jsonb),
            0,
            1
        FROM llm_usage_records r
        WHERE r.usage_source = 'summary'
        ON CONFLICT ON CONSTRAINT uq_summary_updates_settlement DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE summary_updates su
        SET summary_text = s.chat_summary
        FROM chat_history_sessions s
        WHERE s.user_id = su.user_id
          AND s.server_conversation_id = su.conversation_id
          AND COALESCE(s.chat_summary, '') <> ''
          AND su.called_at = (
              SELECT MAX(called_at)
              FROM summary_updates newer
              WHERE newer.user_id = su.user_id
                AND newer.conversation_id = su.conversation_id
          )
        """
    )
    op.execute(
        """
        UPDATE usage_records ur
        SET context = ur.context || jsonb_build_object(
            'conversation_id', match.server_conversation_id,
            'conversation_title', COALESCE(NULLIF(ur.context->>'conversation_title', ''), match.chat_title, '')
        )
        FROM (
            SELECT DISTINCT ON (ur2.id)
                ur2.id,
                s.server_conversation_id,
                s.chat_title
            FROM usage_records ur2
            JOIN chat_history_sessions s
              ON s.user_id = ur2.user_id
             AND s.server_conversation_id IS NOT NULL
            JOIN chat_history_messages m
              ON m.session_id = s.id
             AND jsonb_typeof(m.generated_images) = 'array'
             AND jsonb_array_length(m.generated_images) > 0
            WHERE ur2.source = 'image'
              AND COALESCE(ur2.context->>'conversation_id', '') = ''
              AND abs(EXTRACT(EPOCH FROM (ur2.created_at - s.updated_at))) < 7200
            ORDER BY ur2.id, abs(EXTRACT(EPOCH FROM (ur2.created_at - s.updated_at)))
        ) AS match
        WHERE ur.id = match.id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_summary_updates_user_conversation", table_name="summary_updates")
    op.drop_table("summary_updates")
