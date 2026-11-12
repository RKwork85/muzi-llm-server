"""RBAC + ABAC 权限体系持久化结构。

只描述数据关系，不承载权限决策逻辑。决策逻辑在 app/authorization 模块。

设计要点：
- 角色是权限集合，业务代码不直接判断角色，而是查询统一授权入口。
- 默认拒绝：没有权限码即拒绝。拒绝优先：user_permissions 的 deny 覆盖 allow。
- 资源归属与共享用 resource_grants / resource_shares 表达，与 RBAC 分离。
- 组织与项目仅预留模型，暂不启用数据范围 ORG。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.constants import GRANT_TYPES, ROLE_STATUSES, sql_in
from app.models.mixins import UUIDPrimaryKeyMixin, CreatedAtMixin, TimestampMixin

_JSON_OBJECT = text("'{}'::jsonb")


class Role(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("code", name="uq_roles_code"),
        CheckConstraint(sql_in("status", ROLE_STATUSES), name="ck_roles_status"),
        CheckConstraint("code ~ '^[a-z][a-z0-9_]{1,63}$'", name="ck_roles_code"),
        {"comment": "角色定义。角色只是权限集合，不写死在业务代码里。"},
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(nullable=False, default=False, server_default=text("false"))
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default=text("'active'")
    )


class Permission(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "permissions"
    __table_args__ = (
        UniqueConstraint("code", name="uq_permissions_code"),
        CheckConstraint("code ~ '^[a-z][a-z0-9_]{1,63}(\\.[a-z][a-z0-9_]{1,63})*$'", name="ck_permissions_code"),
        {"comment": "权限字典。code 形如 resource.action。"},
    )

    code: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    resource: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(nullable=False, default=False, server_default=text("false"))


class UserRole(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_role"),
        Index("ix_user_roles_user", "user_id"),
        {"comment": "用户与角色关联。一个用户允许多个角色。"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_user_roles_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("roles.id", name="fk_user_roles_role_id", ondelete="CASCADE"),
        nullable=False,
    )


class RolePermission(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_role_perm"),
        Index("ix_role_permissions_role", "role_id"),
        {"comment": "角色与权限关联。"},
    )

    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("roles.id", name="fk_role_permissions_role_id", ondelete="CASCADE"),
        nullable=False,
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("permissions.id", name="fk_role_permissions_permission_id", ondelete="CASCADE"),
        nullable=False,
    )


class UserPermission(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """用户级直接授权或显式拒绝。deny 优先于 allow，并覆盖角色授权。"""

    __tablename__ = "user_permissions"
    __table_args__ = (
        UniqueConstraint("user_id", "permission_id", name="uq_user_permissions_user_perm"),
        CheckConstraint(sql_in("grant_type", GRANT_TYPES), name="ck_user_permissions_grant"),
        Index("ix_user_permissions_user", "user_id"),
        {"comment": "用户额外授权或显式拒绝。拒绝优先。"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_user_permissions_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("permissions.id", name="fk_user_permissions_permission_id", ondelete="CASCADE"),
        nullable=False,
    )
    grant_type: Mapped[str] = mapped_column(String(32), nullable=False, default="allow")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResourceGrant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """用户对某个具体资源的直接授权或拒绝（超出数据范围的特例）。"""

    __tablename__ = "resource_grants"
    __table_args__ = (
        UniqueConstraint("user_id", "resource_type", "resource_id", name="uq_resource_grants_user_resource"),
        CheckConstraint(sql_in("grant_type", GRANT_TYPES), name="ck_resource_grants_grant"),
        Index("ix_resource_grants_user", "user_id"),
        {"comment": "用户对特定资源的授权。资源归属校验与共享授权分开。"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_resource_grants_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    grant_type: Mapped[str] = mapped_column(String(32), nullable=False, default="allow")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResourceShare(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """资源所有者把资源共享给另一个用户。支撑 SHARED 数据范围。"""

    __tablename__ = "resource_shares"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "grantee_id",
            "resource_type",
            "resource_id",
            "permission",
            name="uq_resource_shares_owner_grantee_resource",
        ),
        Index("ix_resource_shares_owner", "owner_id"),
        Index("ix_resource_shares_grantee", "grantee_id"),
        {"comment": "资源共享授权。资源所有者 share 给其他用户。"},
    )

    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_resource_shares_owner_id", ondelete="CASCADE"),
        nullable=False,
    )
    grantee_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_resource_shares_grantee_id", ondelete="CASCADE"),
        nullable=False,
    )
    permission: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccessAuditLog(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "access_audit_logs"
    __table_args__ = (
        Index("ix_access_audit_logs_actor_created", "actor_id", "created_at"),
        Index("ix_access_audit_logs_subject_created", "subject_id", "created_at"),
        {"comment": "权限决策与敏感操作审计。不保存令牌或请求正文。"},
    )

    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_access_audit_actor_id", ondelete="SET NULL"),
    )
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_access_audit_subject_id", ondelete="SET NULL"),
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    permission: Mapped[str | None] = mapped_column(String(128))
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(128))
    decision: Mapped[str | None] = mapped_column(String(32))
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, default="success", server_default=text("'success'"))
    context_json: Mapped[dict[str, Any]] = mapped_column(
        "context",
        JSONB,
        nullable=False,
        default=dict,
        server_default=_JSON_OBJECT,
    )


class UsageQuota(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "usage_quotas"
    __table_args__ = (
        UniqueConstraint("user_id", "quota_key", "period", name="uq_usage_quotas_user_key_period"),
        CheckConstraint("limit_value >= 0 AND used_value >= 0", name="ck_usage_quotas_non_negative"),
        CheckConstraint("display_value >= 0", name="ck_usage_quotas_display_non_negative"),
        CheckConstraint(
            "limit_source IN ('role_default', 'manual', 'unlimited')",
            name="ck_usage_quotas_limit_source",
        ),
        Index("ix_usage_quotas_user", "user_id"),
        {"comment": "用户额度。limit_source=unlimited 表示不限量；limit_value=0 且非 unlimited 表示禁止使用。"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_usage_quotas_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    quota_key: Mapped[str] = mapped_column(String(64), nullable=False)
    period: Mapped[str] = mapped_column(String(32), nullable=False, default="total", server_default=text("'total'"))
    limit_value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    used_value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default=text("0"))
    limit_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual", server_default=text("'manual'")
    )
    display_value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default=text("0"))
    adjust_reason: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default=text("''"))
    adjusted_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_usage_quotas_adjusted_by", ondelete="SET NULL"),
    )
    adjusted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QuotaAdjustment(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """额度调整流水。数值单位固定为 10 万 Token。"""

    __tablename__ = "quota_adjustments"
    __table_args__ = (
        CheckConstraint(
            "action IN ('increase', 'decrease', 'reset')",
            name="ck_quota_adjustments_action",
        ),
        CheckConstraint("units >= 0 AND unit_size > 0 AND after_limit >= 0", name="ck_quota_adjustments_amounts"),
        Index("ix_quota_adjustments_user_created", "user_id", "created_at"),
        {"comment": "管理端额度调整。units 是 10 万的倍数个数，after_limit 是调整后的上限。"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_quota_adjustments_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_quota_adjustments_actor_id", ondelete="SET NULL"),
    )
    quota_key: Mapped[str] = mapped_column(String(64), nullable=False)
    period: Mapped[str] = mapped_column(String(32), nullable=False, default="total", server_default=text("'total'"))
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    units: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_size: Mapped[int] = mapped_column(Integer, nullable=False, default=100_000, server_default=text("100000"))
    before_limit: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    after_limit: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    before_used: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default=text("0"))
    reason: Mapped[str] = mapped_column(String(200), nullable=False)


class UsageRecord(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        Index("ix_usage_records_user_created", "user_id", "created_at"),
        Index(
            "uq_usage_records_settlement",
            "user_id",
            "source",
            "settlement_key",
            unique=True,
            postgresql_where=text("settlement_key IS NOT NULL"),
        ),
        CheckConstraint(
            "result IN ('settled', 'exceeded', 'unsettled')",
            name="ck_usage_records_result",
        ),
        {"comment": "使用量记录。同一用户、来源、结算键只记一笔。"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_usage_records_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="legacy", server_default=text("'legacy'"))
    settlement_key: Mapped[str | None] = mapped_column(String(160))
    result: Mapped[str] = mapped_column(String(16), nullable=False, default="settled", server_default=text("'settled'"))
    context_json: Mapped[dict[str, Any]] = mapped_column(
        "context",
        JSONB,
        nullable=False,
        default=dict,
        server_default=_JSON_OBJECT,
    )


# ---------------------------------------------------------------------------
# 组织与项目：预留数据模型，暂不启用 ORG 数据范围。
# ---------------------------------------------------------------------------

class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organizations"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_organizations_slug"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_organizations_status"),
        {"comment": "组织。预留模型，暂不启用。"},
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", server_default=text("'active'"))


class OrganizationMember(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "organization_members"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_organization_members_org_user"),
        Index("ix_organization_members_user", "user_id"),
        {"comment": "组织成员。预留模型，暂不启用。"},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", name="fk_org_members_org_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_org_members_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member", server_default=text("'member'"))


class Project(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("organization_id", "slug", name="uq_projects_org_slug"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_projects_status"),
        {"comment": "项目。预留模型，暂不启用。"},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", name="fk_projects_org_id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", server_default=text("'active'"))


class ProjectMember(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_members_project_user"),
        Index("ix_project_members_user", "user_id"),
        {"comment": "项目成员。预留模型，暂不启用。"},
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", name="fk_project_members_project_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_project_members_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member", server_default=text("'member'"))
