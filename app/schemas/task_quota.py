"""任务类型并发额度（全局消息队列管控）的请求/响应结构。"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TaskQuotaRoleLimitIn(BaseModel):
    """任务类型 × 角色的并发额度。"""

    model_config = ConfigDict(extra="forbid")

    role_code: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]{1,63}$")
    quota_limit: int = Field(ge=0)


class TaskQuotaRoleLimitOut(TaskQuotaRoleLimitIn):
    model_config = ConfigDict(from_attributes=True)

    id: UUID


class TaskQuotaConfigIn(BaseModel):
    """创建/更新任务类型额度配置。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    default_quota: int = Field(ge=0)
    role_limits: list[TaskQuotaRoleLimitIn] = Field(default_factory=list)


class TaskQuotaConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_type: str
    name: str
    enabled: bool
    default_quota: int
    role_limits: list[TaskQuotaRoleLimitOut] = Field(default_factory=list)


class UserTaskQuotaOverrideIn(BaseModel):
    """设置单用户任务类型并发额度覆盖。"""

    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    task_type: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]{1,63}$")
    quota_limit: int = Field(ge=0)


class UserTaskQuotaOverrideOut(UserTaskQuotaOverrideIn):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
