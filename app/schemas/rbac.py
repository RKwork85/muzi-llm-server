"""权限体系的请求与响应结构。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.auth import UserOut


class PermissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    resource: str
    action: str
    description: str | None = None
    is_system: bool = False


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    description: str | None = None
    is_system: bool = False
    status: str


class RoleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]{1,63}$")
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)


class RoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    status: str | None = Field(default=None, pattern=r"^(active|disabled)$")


class RolePermissionSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    permission_codes: list[str] = Field(default_factory=list)


class UserRoleAssign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_ids: list[UUID] = Field(default_factory=list)


class UserPermissionGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    grant_type: str = Field(pattern=r"^(allow|deny)$")


class UserPermissionAssign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grants: list[UserPermissionGrant] = Field(default_factory=list)


class UserStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(pattern=r"^(active|disabled)$")


class UserPasswordReset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_password: str = Field(min_length=8, max_length=128)


class UserAdminOut(UserOut):
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    data_scope: str = "self"


class QuotaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    quota_key: str
    period: str
    limit_value: int
    used_value: int


class QuotaSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quota_key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    limit_value: int = Field(ge=0)
    period: str = Field(default="total", max_length=32)


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_id: UUID | None = None
    subject_id: UUID | None = None
    action: str
    permission: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    decision: str | None = None
    outcome: str
    context: dict = Field(default_factory=dict)
    created_at: datetime


class MeResponse(BaseModel):
    user: UserOut
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    data_scope: str = "self"
