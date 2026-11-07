"""邮箱注册和登录。只接收参数并返回结果。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.database.deps import DbSession
from app.schemas.auth import AuthResponse, LoginRequest, RegisterRequest, UserOut
from app.schemas.common import ErrorResponse
from app.services.auth_service import AuthService, IssuedLogin

router = APIRouter(prefix="/auth", tags=["auth"])


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
