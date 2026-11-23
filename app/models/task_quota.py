"""任务类型额度配置：全局消息队列管控的并发额度（并发在途任务数）。

与 token 计费额度（``UsageQuota``）是两回事：这里是「某用户在某任务类型下最多同时
进行中的生成任务数」，任务完成即释放，用于防滥用与队列公平。额度值由后台动态配置，
按「单用户覆盖 → 角色额度（取最高角色）→ 默认额度」的优先级解析。
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.mixins import UUIDPrimaryKeyMixin, TimestampMixin


class TaskQuotaConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """任务类型定义：额度管控的配置维度（如 create_media）。"""

    __tablename__ = "task_quota_configs"
    __table_args__ = (
        UniqueConstraint("task_type", name="uq_task_quota_configs_task_type"),
        CheckConstraint("default_quota >= 0", name="ck_task_quota_configs_default_quota"),
        CheckConstraint(
            "task_type ~ '^[a-z][a-z0-9_]{1,63}$'",
            name="ck_task_quota_configs_task_type",
        ),
        {"comment": "任务类型额度配置。default_quota 为未匹配角色的兜底并发额度（0=不限）。"},
    )

    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    default_quota: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default=text("3")
    )


class TaskQuotaRoleLimit(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """任务类型 × 角色 的并发额度。"""

    __tablename__ = "task_quota_role_limits"
    __table_args__ = (
        UniqueConstraint("task_type", "role_code", name="uq_task_quota_role_limits_type_role"),
        CheckConstraint("quota_limit >= 0", name="ck_task_quota_role_limits_quota_limit"),
        CheckConstraint(
            "role_code ~ '^[a-z][a-z0-9_]{1,63}$'",
            name="ck_task_quota_role_limits_role",
        ),
        Index("ix_task_quota_role_limits_task_type", "task_type"),
        {"comment": "任务类型 × 角色的并发额度。quota_limit=0 表示不限。"},
    )

    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    role_code: Mapped[str] = mapped_column(String(64), nullable=False)
    quota_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default=text("3")
    )


class UserTaskQuotaOverride(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """单用户任务类型额度覆盖。优先级高于角色额度。"""

    __tablename__ = "user_task_quota_overrides"
    __table_args__ = (
        UniqueConstraint("user_id", "task_type", name="uq_user_task_quota_overrides_user_type"),
        CheckConstraint("quota_limit >= 0", name="ck_user_task_quota_overrides_quota_limit"),
        Index("ix_user_task_quota_overrides_user", "user_id"),
        {"comment": "单用户任务类型并发额度覆盖。优先级高于角色额度。"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_user_task_quota_overrides_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    quota_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default=text("3")
    )
