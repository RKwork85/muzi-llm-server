"""Persistent planning, interaction, and event outbox records."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.mixins import CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin

_JSON_OBJECT = text("'{}'::jsonb")


class PersistentPlan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "plans"
    __table_args__ = (
        CheckConstraint("status IN ('understanding', 'clarifying', 'drafting', 'awaiting_approval', 'approved', 'ready_for_handoff', 'executing', 'completed', 'paused', 'cancelled', 'failed', 'superseded')", name="ck_plans_status"),
        CheckConstraint("current_revision >= 0", name="ck_plans_revision"),
        Index("ix_plans_owner_updated", "owner_id", "updated_at"),
        Index("ix_plans_conversation_updated", "conversation_id", "updated_at"),
        {"comment": "Interactive Planning aggregate; owner is derived from the conversation."},
    )

    public_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sessions.id", name="fk_plans_conversation_id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_plans_owner_id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class PersistentPlanRevision(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "plan_revisions"
    __table_args__ = (
        CheckConstraint("revision >= 0", name="ck_plan_revisions_revision"),
        UniqueConstraint("plan_id", "revision", name="uq_plan_revisions_revision"),
        Index("ix_plan_revisions_plan_created", "plan_id", "created_at"),
        {"comment": "Immutable versioned plan snapshot."},
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plans.id", name="fk_plan_revisions_plan_id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class PersistentPlanDecision(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "plan_decisions"
    __table_args__ = (
        CheckConstraint("source IN ('user', 'assumed', 'system')", name="ck_plan_decisions_source"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_plan_decisions_confidence"),
        Index("ix_plan_decisions_active", "plan_id", "active"),
        Index("ix_plan_decisions_revision", "plan_id", "revision"),
        {"comment": "Versioned user, assumed, or system decision with dependencies."},
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plans.id", name="fk_plan_decisions_plan_id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float | None]
    dependencies: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))


class PersistentInteraction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "plan_interactions"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'submitted', 'processing', 'completed', 'failed', 'skipped', 'expired', 'superseded', 'cancelled')", name="ck_plan_interactions_status"),
        UniqueConstraint("public_id", name="uq_plan_interactions_public_id"),
        Index("ix_plan_interactions_pending", "plan_id", "status", "created_at"),
        {"comment": "Validated versioned user interaction card."},
    )

    public_id: Mapped[str] = mapped_column(String(128), nullable=False)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plans.id", name="fk_plan_interactions_plan_id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PersistentInteractionSubmission(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "interaction_submissions"
    __table_args__ = (
        UniqueConstraint("plan_id", "client_submission_id", name="uq_interaction_submissions_idempotency"),
        Index("ix_interaction_submissions_owner_created", "owner_id", "created_at"),
        {"comment": "Idempotent interaction response and its committed result snapshot."},
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plans.id", name="fk_interaction_submissions_plan_id", ondelete="CASCADE"),
        nullable=False,
    )
    interaction_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plan_interactions.id", name="fk_interaction_submissions_interaction_id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_interaction_submissions_owner_id", ondelete="CASCADE"),
        nullable=False,
    )
    client_submission_id: Mapped[str] = mapped_column(String(128), nullable=False)
    expected_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    result_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class PersistentPlanAction(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """M2 统一交互动作（InteractionAction）的幂等记录与结果快照。"""

    __tablename__ = "plan_actions"
    __table_args__ = (
        UniqueConstraint("plan_id", "idempotency_key", name="uq_plan_actions_idempotency"),
        Index("ix_plan_actions_owner_created", "owner_id", "created_at"),
        {"comment": "Idempotent unified interaction action and its committed result snapshot."},
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plans.id", name="fk_plan_actions_plan_id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_plan_actions_owner_id", ondelete="CASCADE"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(128))
    interaction_id: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default=_JSON_OBJECT)
    result_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    result_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class PersistentPlanningEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "plan_events"
    __table_args__ = (
        CheckConstraint("sequence >= 1", name="ck_plan_events_sequence"),
        UniqueConstraint("conversation_id", "sequence", name="uq_plan_events_sequence"),
        UniqueConstraint("event_id", name="uq_plan_events_event_id"),
        Index("ix_plan_events_conversation_sequence", "conversation_id", "sequence"),
        {"comment": "Ordered durable planning event log."},
    )

    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plans.id", name="fk_plan_events_plan_id", ondelete="CASCADE"),
        nullable=True,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sessions.id", name="fk_plan_events_conversation_id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default=_JSON_OBJECT)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PersistentPlanningOutbox(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "plan_event_outbox"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_plan_event_outbox_event"),
        Index("ix_plan_event_outbox_pending", "delivered_at", "created_at"),
        {"comment": "Transactional outbox for durable planning event delivery."},
    )

    event_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("plan_events.event_id", name="fk_plan_event_outbox_event", ondelete="CASCADE"),
        nullable=False,
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
