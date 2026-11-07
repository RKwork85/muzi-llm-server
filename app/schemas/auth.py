"""邮箱注册和登录的请求与响应。不返回密码或密码哈希。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=320)
    username: str = Field(max_length=32)
    password: str = Field(max_length=128)
    display_name: str | None = Field(default=None, max_length=120)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=320)
    password: str = Field(max_length=128)


class UserOut(BaseModel):
    id: UUID
    email: str
    username: str
    display_name: str | None
    status: str


class AuthResponse(BaseModel):
    user: UserOut
    token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
