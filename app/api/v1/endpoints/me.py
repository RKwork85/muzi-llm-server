"""当前登录用户信息与有效权限。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import get_authenticated_user
from app.authorization.application.authorization_service import AuthorizationService
from app.core.database.deps import DbSession
from app.models.tables import User
from app.schemas.auth import UserOut
from app.schemas.preferences import (
    BackgroundSettings,
    DEFAULT_COLOR_CHAT,
    DEFAULT_COLOR_PRIMARY,
    UserPreferencesOut,
    UserPreferencesUpdate,
)
from app.schemas.rbac import MeResponse

router = APIRouter(prefix="/me", tags=["me"])


@router.get("", response_model=MeResponse, summary="当前用户信息与有效权限")
async def get_me(
    user: Annotated[User, Depends(get_authenticated_user)],
    session: DbSession,
) -> MeResponse:
    snapshot = await AuthorizationService(session).snapshot(user.id)
    return MeResponse(
        user=UserOut(
            id=user.id,
            email=user.email,
            username=user.username,
            display_name=user.display_name,
            status=user.status,
        ),
        roles=list(snapshot.roles),
        permissions=sorted(snapshot.permissions),
        data_scope=snapshot.data_scope,
    )


def _preferences_out(user: User) -> UserPreferencesOut:
    """从用户偏好 JSONB 读出背景设置，缺失字段回退默认值。"""
    background = (user.preferences or {}).get("background") or {}
    return UserPreferencesOut(
        background=BackgroundSettings(
            color_chat=background.get("color_chat", DEFAULT_COLOR_CHAT),
            color_primary=background.get("color_primary", DEFAULT_COLOR_PRIMARY),
        )
    )


@router.get("/preferences", response_model=UserPreferencesOut, summary="读取我的通用设置")
async def get_preferences(
    user: Annotated[User, Depends(get_authenticated_user)],
) -> UserPreferencesOut:
    return _preferences_out(user)


@router.put("/preferences", response_model=UserPreferencesOut, summary="更新我的通用设置")
async def update_preferences(
    body: UserPreferencesUpdate,
    user: Annotated[User, Depends(get_authenticated_user)],
    session: DbSession,
) -> UserPreferencesOut:
    preferences = dict(user.preferences or {})
    if body.background is not None:
        preferences["background"] = {
            "color_chat": body.background.color_chat,
            "color_primary": body.background.color_primary,
        }
    user.preferences = preferences
    await session.flush()
    return _preferences_out(user)
