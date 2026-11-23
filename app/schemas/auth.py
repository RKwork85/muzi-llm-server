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
    # 邮箱验证码。邮件服务开启时必填，用来证明邮箱归属。
    code: str | None = Field(default=None, min_length=4, max_length=8)


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
    """发送验证码成功后的响应。短信与邮箱共用。"""

    ok: bool = True
    cooldown_seconds: int = 60
    expires_in: int | None = None
    message: str = "验证码已发送"


class EmailSendCodeRequest(BaseModel):
    """发送邮箱验证码。注册场景给未注册邮箱发，绑定/改密场景给已注册邮箱发。"""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=320, description="接收验证码的邮箱地址")
    scene: Literal["register", "bind", "reset_password"] = "register"


class EmailRegisterRequest(BaseModel):
    """邮箱 + 验证码注册。需要已经通过 /email/send-code 拿到验证码。"""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=320, description="待注册的邮箱地址")
    username: str = Field(max_length=32)
    password: str = Field(max_length=128)
    code: str = Field(min_length=4, max_length=8, description="邮箱验证码")



class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    old_password: str = Field(max_length=128)
    new_password: str = Field(max_length=128)


class ResetPasswordRequest(BaseModel):
    """凭邮箱验证码重置密码。不需要登录态，也不需要旧密码。"""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=320, description="已注册的邮箱地址")
    code: str = Field(min_length=4, max_length=8, description="邮箱验证码")
    new_password: str = Field(max_length=128)


class ChangeUsernameRequest(BaseModel):
    """修改用户名。实际格式校验在服务层完成，保证与注册时的规则完全一致。"""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(
        max_length=32,
        description="2-32 个字符，以字母或汉字开头，可包含字母、汉字、数字和下划线",
    )


class DemoAccountRequest(BaseModel):
    """随机演示账号（登录页图标彩蛋，仅开发环境）。"""

    model_config = ConfigDict(extra="forbid")

    role: Literal["member", "developer"] = Field(
        default="member",
        description="member：与普通用户权限一致；developer：开发者角色",
    )


class DemoAccountOut(BaseModel):
    """演示账号凭据。密码为一次性明文回传，仅用于前端自动填充登录表单。"""

    email: str
    username: str
    password: str
    role: str
    role_name: str


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
