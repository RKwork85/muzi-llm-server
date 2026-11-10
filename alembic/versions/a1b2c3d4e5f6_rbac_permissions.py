"""rbac permissions

Revision ID: a1b2c3d4e5f6
Revises: 9a3c5e7f1b2d
Create Date: 2026-11-10 01:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "9a3c5e7f1b2d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'active'"), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("code ~ '^[a-z][a-z0-9_]{1,63}$'", name="ck_roles_code"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_roles_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_roles_code"),
        comment="角色定义。角色只是权限集合，不写死在业务代码里。",
    )

    op.create_table(
        "permissions",
        sa.Column("code", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("resource", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("code ~ '^[a-z][a-z0-9_]{1,63}(\\.[a-z][a-z0-9_]{1,63})*$'", name="ck_permissions_code"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_permissions_code"),
        comment="权限字典。code 形如 resource.action。",
    )

    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], name="fk_user_roles_role_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_roles_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_role"),
        comment="用户与角色关联。一个用户允许多个角色。",
    )
    op.create_index("ix_user_roles_user", "user_roles", ["user_id"])

    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"], name="fk_role_permissions_permission_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], name="fk_role_permissions_role_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_role_perm"),
        comment="角色与权限关联。",
    )
    op.create_index("ix_role_permissions_role", "role_permissions", ["role_id"])

    op.create_table(
        "user_permissions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("permission_id", sa.Uuid(), nullable=False),
        sa.Column("grant_type", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("grant_type IN ('allow', 'deny')", name="ck_user_permissions_grant"),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"], name="fk_user_permissions_permission_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_permissions_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "permission_id", name="uq_user_permissions_user_perm"),
        comment="用户额外授权或显式拒绝。拒绝优先。",
    )
    op.create_index("ix_user_permissions_user", "user_permissions", ["user_id"])

    op.create_table(
        "resource_grants",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=False),
        sa.Column("grant_type", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("grant_type IN ('allow', 'deny')", name="ck_resource_grants_grant"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_resource_grants_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "resource_type", "resource_id", name="uq_resource_grants_user_resource"),
        comment="用户对特定资源的授权。资源归属校验与共享授权分开。",
    )
    op.create_index("ix_resource_grants_user", "resource_grants", ["user_id"])

    op.create_table(
        "resource_shares",
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("grantee_id", sa.Uuid(), nullable=False),
        sa.Column("permission", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["grantee_id"], ["users.id"], name="fk_resource_shares_grantee_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_resource_shares_owner_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "grantee_id", "resource_type", "resource_id", "permission",
            name="uq_resource_shares_owner_grantee_resource",
        ),
        comment="资源共享授权。资源所有者 share 给其他用户。",
    )
    op.create_index("ix_resource_shares_owner", "resource_shares", ["owner_id"])
    op.create_index("ix_resource_shares_grantee", "resource_shares", ["grantee_id"])

    op.create_table(
        "access_audit_logs",
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("subject_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("permission", sa.String(length=128), nullable=True),
        sa.Column("resource_type", sa.String(length=64), nullable=True),
        sa.Column("resource_id", sa.String(length=128), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=True),
        sa.Column("outcome", sa.String(length=32), server_default=sa.text("'success'"), nullable=False),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], name="fk_access_audit_actor_id", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["subject_id"], ["users.id"], name="fk_access_audit_subject_id", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        comment="权限决策与敏感操作审计。不保存令牌或请求正文。",
    )
    op.create_index("ix_access_audit_logs_actor_created", "access_audit_logs", ["actor_id", "created_at"])
    op.create_index("ix_access_audit_logs_subject_created", "access_audit_logs", ["subject_id", "created_at"])

    op.create_table(
        "usage_quotas",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("quota_key", sa.String(length=64), nullable=False),
        sa.Column("period", sa.String(length=32), server_default=sa.text("'total'"), nullable=False),
        sa.Column("limit_value", sa.Integer(), nullable=False),
        sa.Column("used_value", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("limit_value >= 0 AND used_value >= 0", name="ck_usage_quotas_non_negative"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_usage_quotas_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "quota_key", "period", name="uq_usage_quotas_user_key_period"),
        comment="用户额度。配额与权限分离，不与角色混用。",
    )
    op.create_index("ix_usage_quotas_user", "usage_quotas", ["user_id"])

    op.create_table(
        "usage_records",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_usage_records_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="使用量记录。用于审计与账单核算。",
    )
    op.create_index("ix_usage_records_user_created", "usage_records", ["user_id", "created_at"])

    op.create_table(
        "organizations",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'active'"), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_organizations_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
        comment="组织。预留模型，暂不启用。",
    )

    op.create_table(
        "organization_members",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), server_default=sa.text("'member'"), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_org_members_org_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_org_members_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_organization_members_org_user"),
        comment="组织成员。预留模型，暂不启用。",
    )
    op.create_index("ix_organization_members_user", "organization_members", ["user_id"])

    op.create_table(
        "projects",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'active'"), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_projects_status"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_projects_org_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "slug", name="uq_projects_org_slug"),
        comment="项目。预留模型，暂不启用。",
    )

    op.create_table(
        "project_members",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), server_default=sa.text("'member'"), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_project_members_project_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_project_members_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_members_project_user"),
        comment="项目成员。预留模型，暂不启用。",
    )
    op.create_index("ix_project_members_user", "project_members", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_project_members_user", table_name="project_members")
    op.drop_table("project_members")
    op.drop_table("projects")
    op.drop_index("ix_organization_members_user", table_name="organization_members")
    op.drop_table("organization_members")
    op.drop_table("organizations")
    op.drop_index("ix_usage_records_user_created", table_name="usage_records")
    op.drop_table("usage_records")
    op.drop_index("ix_usage_quotas_user", table_name="usage_quotas")
    op.drop_table("usage_quotas")
    op.drop_index("ix_access_audit_logs_subject_created", table_name="access_audit_logs")
    op.drop_index("ix_access_audit_logs_actor_created", table_name="access_audit_logs")
    op.drop_table("access_audit_logs")
    op.drop_index("ix_resource_shares_grantee", table_name="resource_shares")
    op.drop_index("ix_resource_shares_owner", table_name="resource_shares")
    op.drop_table("resource_shares")
    op.drop_index("ix_resource_grants_user", table_name="resource_grants")
    op.drop_table("resource_grants")
    op.drop_index("ix_user_permissions_user", table_name="user_permissions")
    op.drop_table("user_permissions")
    op.drop_index("ix_role_permissions_role", table_name="role_permissions")
    op.drop_table("role_permissions")
    op.drop_index("ix_user_roles_user", table_name="user_roles")
    op.drop_table("user_roles")
    op.drop_table("permissions")
    op.drop_table("roles")
