"""邮箱注册、登录、退出登录与修改密码。只接收参数并返回结果。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.deps import get_authenticated_user
from app.core.database.deps import DbSession
from app.models.tables import User
from app.schemas.auth import (
    AuthResponse,
    ChangePasswordRequest,
    LoginRequest,
    PhoneLoginRequest,
    PhoneSendCodeRequest,
    PhoneSendCodeResponse,
    RegisterRequest,
    UserOut,
)
from app.schemas.common import ErrorResponse
from app.services.auth_service import AuthService, IssuedLogin

router = APIRouter(prefix="/auth", tags=["auth"])
AuthenticatedUser = Annotated[User, Depends(get_authenticated_user)]
_bearer = HTTPBearer(auto_error=False)


def _auth_errors() -> dict[int, dict[str, Any]]:
    return {
        401: {"model": ErrorResponse, "description": "邮箱或密码错误"},
        403: {"model": ErrorResponse, "description": "账户已停用"},
        409: {"model": ErrorResponse, "description": "邮箱或用户名已被使用"},
        422: {"model": ErrorResponse, "description": "邮箱、用户名或密码不符合要求"},
        503: {"model": ErrorResponse, "description": "数据库未就绪"},
    }


def _response(issued: IssuedLogin) -> AuthResponse:
    user = issued.user
    return AuthResponse(
        user=UserOut(
            id=user.id,
            email=user.email,
            username=user.username,
            display_name=user.display_name,
            status=user.status,
            avatar=user.avatar,
            phone=user.phone,
        ),
        token=issued.token,
        expires_at=issued.expires_at,
    )


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=201,
    responses=_auth_errors(),
    summary="邮箱注册",
)
async def register(body: RegisterRequest, session: DbSession) -> AuthResponse:
    issued = await AuthService(session).register(
        body.email,
        body.username,
        body.password,
        body.display_name,
    )
    return _response(issued)


@router.post(
    "/login",
    response_model=AuthResponse,
    responses=_auth_errors(),
    summary="邮箱登录",
)
async def login(body: LoginRequest, session: DbSession) -> AuthResponse:
    issued = await AuthService(session).login(body.email, body.password)
    return _response(issued)


@router.post(
    "/phone/send-code",
    response_model=PhoneSendCodeResponse,
    responses={
        422: {"model": ErrorResponse, "description": "手机号或场景无效"},
        429: {"model": ErrorResponse, "description": "发送过于频繁"},
        501: {"model": ErrorResponse, "description": "短信服务尚未配置"},
        502: {"model": ErrorResponse, "description": "短信通道发送失败"},
    },
    summary="发送手机验证码",
)
async def send_phone_code(body: PhoneSendCodeRequest, session: DbSession) -> PhoneSendCodeResponse:
    """校验手机号后写入验证码并调用阿里云短信下发。"""
    result = await AuthService(session).send_phone_code(body.phone, body.scene)
    return PhoneSendCodeResponse(**result)


@router.post(
    "/phone/login",
    response_model=AuthResponse,
    responses={
        401: {"model": ErrorResponse, "description": "验证码错误或已失效"},
        403: {"model": ErrorResponse, "description": "账户已停用"},
        422: {"model": ErrorResponse, "description": "手机号或验证码无效"},
        501: {"model": ErrorResponse, "description": "短信服务尚未配置"},
        503: {"model": ErrorResponse, "description": "验证码服务暂不可用"},
    },
    summary="手机号验证码登录",
)
async def login_with_phone(body: PhoneLoginRequest, session: DbSession) -> AuthResponse:
    """手机号 + 验证码登录。未注册时自动创建账户。"""
    issued = await AuthService(session).login_with_phone(body.phone, body.code)
    return _response(issued)


@router.post("/logout", summary="退出登录")
async def logout(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: DbSession,
) -> dict[str, Any]:
    """吊销当前登录凭证。幂等：token 缺失或已失效时不报错。"""
    if credentials is not None and credentials.scheme.lower() == "bearer":
        await AuthService(session).revoke_token(credentials.credentials)
    return {"ok": True}


@router.post(
    "/change-password",
    summary="修改账号密码",
    responses={
        401: {"model": ErrorResponse, "description": "当前密码不正确"},
        422: {"model": ErrorResponse, "description": "新密码不符合要求"},
    },
)
async def change_password(
    body: ChangePasswordRequest,
    session: DbSession,
    user: AuthenticatedUser,
) -> dict[str, Any]:
    """校验旧密码后更新为新密码，并吊销该用户全部登录凭证。"""
    await AuthService(session).change_password(user.id, body.old_password, body.new_password)
    return {"ok": True}
