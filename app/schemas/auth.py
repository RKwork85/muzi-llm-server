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


class PhoneSendCodeRequest(BaseModel):
    """发送手机验证码（预留）。scene: login / bind。"""

    model_config = ConfigDict(extra="forbid")

    phone: str = Field(max_length=20, description="中国大陆手机号，11 位")
    scene: Literal["login", "bind"] = "login"


class PhoneLoginRequest(BaseModel):
    """手机号 + 验证码登录/注册（预留）。"""

    model_config = ConfigDict(extra="forbid")

    phone: str = Field(max_length=20, description="中国大陆手机号，11 位")
    code: str = Field(min_length=4, max_length=8, description="短信验证码")


class PhoneSendCodeResponse(BaseModel):
    """发送验证码成功后的占位响应。"""

    ok: bool = True
    cooldown_seconds: int = 60
    message: str = "验证码已发送"


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    old_password: str = Field(max_length=128)
    new_password: str = Field(max_length=128)


class UserOut(BaseModel):
    id: UUID
    email: str
    username: str
    display_name: str | None
    status: str
    avatar: str | None = None
    phone: str | None = None


class AuthResponse(BaseModel):
    user: UserOut
    token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
