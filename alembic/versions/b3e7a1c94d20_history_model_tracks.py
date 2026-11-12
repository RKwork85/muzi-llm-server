"""一段对话分别记下文本和图片两条模型历史。

Revision ID: b3e7a1c94d20
Revises: a8d4e2c91b07
Create Date: 2026-11-12 17:40:00.000000

"""
from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3e7a1c94d20"
down_revision: Union[str, Sequence[str], None] = "a8d4e2c91b07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_history_sessions",
        sa.Column(
            "model_tracks",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("chat_history_messages", sa.Column("history_track", sa.String(length=16), nullable=True))
    op.add_column(
        "chat_history_messages",
        sa.Column("track_model", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    _backfill()


def _backfill() -> None:
    from app.services.history_service import annotate_history_tracks

    bind = op.get_bind()
    sessions = list(
        bind.execute(
            sa.text(
                """
                SELECT id, model_id, model_name, provider_id, model_category, model_api, extra
                FROM chat_history_sessions
                """
            )
        ).mappings()
    )
    for session in sessions:
        rows = bind.execute(
            sa.text(
                """
                SELECT id, seq, role, usage, generated_images, planning
                FROM chat_history_messages
                WHERE session_id = :sid
                ORDER BY seq
                """
            ),
            {"sid": session["id"]},
        ).mappings().all()
        extra = session["extra"] if isinstance(session["extra"], dict) else {}
        usage = extra.get("tokenUsage") if isinstance(extra.get("tokenUsage"), dict) else {}
        messages = []
        for row in rows:
            planning = row["planning"] if isinstance(row["planning"], dict) else {}
            message = {
                "role": row["role"],
                "usage": row["usage"] if isinstance(row["usage"], dict) else None,
                "generated_images": row["generated_images"] if isinstance(row["generated_images"], list) else [],
            }
            for key in ("generating_images", "gen_image_error", "gen_image_meta"):
                if key in planning:
                    message[key] = planning[key]
            messages.append(message)
        tracks = annotate_history_tracks(
            messages,
            {
                "modelId": session["model_id"],
                "modelName": session["model_name"],
                "providerId": session["provider_id"],
                "modelCategory": session["model_category"],
                "modelApi": session["model_api"],
                "tokenUsage": usage,
            },
        )
        for row, message in zip(rows, messages):
            track = message.get("historyTrack")
            model = message.get("trackModel") if isinstance(message.get("trackModel"), dict) else None
            bind.execute(
                sa.text(
                    """
                    UPDATE chat_history_messages
                    SET history_track = :track,
                        track_model = CAST(:model AS jsonb)
                    WHERE id = :id
                    """
                ),
                {
                    "id": row["id"],
                    "track": track if track in {"text", "image"} else None,
                    "model": json.dumps(model, ensure_ascii=False) if model else None,
                },
            )
        bind.execute(
            sa.text(
                """
                UPDATE chat_history_sessions
                SET model_tracks = CAST(:tracks AS jsonb)
                WHERE id = :id
                """
            ),
            {"id": session["id"], "tracks": json.dumps(tracks, ensure_ascii=False)},
        )


def downgrade() -> None:
    op.drop_column("chat_history_messages", "track_model")
    op.drop_column("chat_history_messages", "history_track")
    op.drop_column("chat_history_sessions", "model_tracks")
