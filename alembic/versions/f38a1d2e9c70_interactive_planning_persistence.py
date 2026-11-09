"""interactive planning persistence

Revision ID: f38a1d2e9c70
Revises: c4a8e2b71f03
Create Date: 2026-11-09 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f38a1d2e9c70"
down_revision: Union[str, Sequence[str], None] = "c4a8e2b71f03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("public_id", sa.String(length=128), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["sessions.id"], name="fk_plans_conversation_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_plans_owner_id", ondelete="CASCADE"),
        sa.CheckConstraint("status IN ('understanding', 'clarifying', 'drafting', 'awaiting_approval', 'approved', 'ready_for_handoff', 'executing', 'completed', 'paused', 'cancelled', 'failed', 'superseded')", name="ck_plans_status"),
        sa.CheckConstraint("current_revision >= 0", name="ck_plans_revision"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id", name="uq_plans_public_id"),
        comment="Interactive Planning aggregate; owner is derived from the conversation.",
    )
    op.create_index("ix_plans_owner_updated", "plans", ["owner_id", "updated_at"])
    op.create_index("ix_plans_conversation_updated", "plans", ["conversation_id", "updated_at"])

    op.create_table(
        "plan_revisions",
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], name="fk_plan_revisions_plan_id", ondelete="CASCADE"),
        sa.CheckConstraint("revision >= 0", name="ck_plan_revisions_revision"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "revision", name="uq_plan_revisions_revision"),
        comment="Immutable versioned plan snapshot.",
    )
    op.create_index("ix_plan_revisions_plan_created", "plan_revisions", ["plan_id", "created_at"])

    op.create_table(
        "plan_decisions",
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("decision_key", sa.String(length=128), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("dependencies", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], name="fk_plan_decisions_plan_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("source IN ('user', 'assumed', 'system')", name="ck_plan_decisions_source"),
        sa.CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_plan_decisions_confidence"),
        comment="Versioned user, assumed, or system decision with dependencies.",
    )
    op.create_index("ix_plan_decisions_active", "plan_decisions", ["plan_id", "active"])
    op.create_index("ix_plan_decisions_revision", "plan_decisions", ["plan_id", "revision"])

    op.create_table(
        "plan_interactions",
        sa.Column("public_id", sa.String(length=128), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("plan_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], name="fk_plan_interactions_plan_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id", name="uq_plan_interactions_public_id"),
        sa.CheckConstraint("status IN ('pending', 'submitted', 'skipped', 'expired', 'superseded', 'cancelled')", name="ck_plan_interactions_status"),
        comment="Validated versioned user interaction card.",
    )
    op.create_index("ix_plan_interactions_pending", "plan_interactions", ["plan_id", "status", "created_at"])

    op.create_table(
        "interaction_submissions",
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("interaction_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("client_submission_id", sa.String(length=128), nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], name="fk_interaction_submissions_plan_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["interaction_id"], ["plan_interactions.id"], name="fk_interaction_submissions_interaction_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_interaction_submissions_owner_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "client_submission_id", name="uq_interaction_submissions_idempotency"),
        comment="Idempotent interaction response and its committed result snapshot.",
    )
    op.create_index("ix_interaction_submissions_owner_created", "interaction_submissions", ["owner_id", "created_at"])

    op.create_table(
        "plan_events",
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], name="fk_plan_events_plan_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["sessions.id"], name="fk_plan_events_conversation_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("sequence >= 1", name="ck_plan_events_sequence"),
        sa.UniqueConstraint("conversation_id", "sequence", name="uq_plan_events_sequence"),
        sa.UniqueConstraint("event_id", name="uq_plan_events_event_id"),
        comment="Ordered durable planning event log.",
    )
    op.create_index("ix_plan_events_conversation_sequence", "plan_events", ["conversation_id", "sequence"])

    op.create_table(
        "plan_event_outbox",
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["plan_events.event_id"], name="fk_plan_event_outbox_event", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_plan_event_outbox_event"),
        comment="Transactional outbox for durable planning event delivery.",
    )
    op.create_index("ix_plan_event_outbox_pending", "plan_event_outbox", ["delivered_at", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_plan_event_outbox_pending", table_name="plan_event_outbox")
    op.drop_table("plan_event_outbox")
    op.drop_index("ix_plan_events_conversation_sequence", table_name="plan_events")
    op.drop_table("plan_events")
    op.drop_index("ix_interaction_submissions_owner_created", table_name="interaction_submissions")
    op.drop_table("interaction_submissions")
    op.drop_index("ix_plan_interactions_pending", table_name="plan_interactions")
    op.drop_table("plan_interactions")
    op.drop_index("ix_plan_decisions_revision", table_name="plan_decisions")
    op.drop_index("ix_plan_decisions_active", table_name="plan_decisions")
    op.drop_table("plan_decisions")
    op.drop_index("ix_plan_revisions_plan_created", table_name="plan_revisions")
    op.drop_table("plan_revisions")
    op.drop_index("ix_plans_conversation_updated", table_name="plans")
    op.drop_index("ix_plans_owner_updated", table_name="plans")
    op.drop_table("plans")
