"""公开分享对话接口：登录用户创建分享，任何人凭 share_id 只读访问。"""

from __future__ import annotations

import secrets
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from sqlalchemy import delete, select

from app.api.deps import get_authenticated_user, get_settings_dep
from app.core.config import Settings
from app.core.database.deps import DbSession
from app.core.exceptions import ShareNotFoundError
from app.data.oss_client import OssUploadClient
from app.models.tables import ChatShare, User
from app.schemas.share import (
    ShareCreateRequest,
    ShareCreateResponse,
    ShareListItem,
    ShareListResponse,
    SharePublicResponse,
)
from app.services.history_service import persist_ark_images

router = APIRouter(prefix="/share", tags=["share"])
AuthenticatedUser = Annotated[User, Depends(get_authenticated_user)]
AppSettings = Annotated[Settings, Depends(get_settings_dep)]

DEFAULT_PUBLIC_ORIGIN = "https://muzihhh.com"


def _oss_client(request: Request) -> OssUploadClient | None:
    chat_service = getattr(request.app.state, "chat_service", None)
    return getattr(chat_service, "oss_client", None)


def _request_origin(request: Request) -> str:
    """从请求头还原前端站点，用于开发环境拼接可点击的分享链接。"""
    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin:
        try:
            parsed = urlparse(origin)
            if parsed.scheme in {"http", "https"} and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}"
        except ValueError:
            pass
    return ""


def _site_origin(request: Request, settings: Settings) -> str:
    """分享链接的站点根地址。

    - 生产环境：固定使用配置的公开域名（默认 https://muzihhh.com）。
    - 开发 / 测试环境：优先使用请求来源的 host，回落到配置值。
    """
    configured = (settings.share_public_base_url or "").strip().rstrip("/")
    if (settings.runtime_env or "").strip().lower() == "production":
        return configured or DEFAULT_PUBLIC_ORIGIN
    return _request_origin(request) or configured or DEFAULT_PUBLIC_ORIGIN


def _new_share_id() -> str:
    """生成高熵、URL 安全的公开分享码。"""
    return secrets.token_urlsafe(16)


@router.post("", response_model=ShareCreateResponse, summary="创建公开分享链接")
async def create_share(
    body: ShareCreateRequest,
    request: Request,
    session: DbSession,
    user: AuthenticatedUser,
    settings: AppSettings,
) -> ShareCreateResponse:
    snapshot = dict(body.snapshot or {})
    title = (body.title or snapshot.get("chatTitle") or "").strip()[:200]
    chat_id = body.chat_id or str(snapshot.get("chatId") or "")

    # 把方舟临时图片转存 OSS，避免分享链接随临时凭证失效。
    await persist_ark_images(snapshot, _oss_client(request), chat_id, str(user.id))

    share = ChatShare(
        share_id=_new_share_id(),
        owner_id=user.id,
        chat_id=chat_id[:64],
        title=title,
        snapshot=snapshot,
        source_name=(user.display_name or user.username or "").strip()[:120],
        source_username=user.username,
        source_avatar=user.avatar,
    )
    session.add(share)
    await session.flush()

    origin = _site_origin(request, settings)
    return ShareCreateResponse(share_id=share.share_id, url=f"{origin}/share/{share.share_id}")


@router.get("", response_model=ShareListResponse, summary="列出当前用户创建的分享链接")
async def list_my_shares(
    request: Request,
    session: DbSession,
    user: AuthenticatedUser,
    settings: AppSettings,
) -> ShareListResponse:
    rows = list(
        await session.scalars(
            select(ChatShare)
            .where(ChatShare.owner_id == user.id)
            .order_by(ChatShare.created_at.desc())
        )
    )
    origin = _site_origin(request, settings)
    items = [
        ShareListItem(
            share_id=row.share_id,
            title=row.title or "未命名对话",
            url=f"{origin}/share/{row.share_id}",
            source_name=row.source_name,
            created_at=row.created_at,
        )
        for row in rows
    ]
    return ShareListResponse(items=items)


@router.delete("/{share_id}", summary="删除当前用户创建的分享链接")
async def delete_share(
    share_id: str,
    session: DbSession,
    user: AuthenticatedUser,
) -> dict[str, object]:
    result = await session.execute(
        delete(ChatShare).where(
            ChatShare.share_id == share_id,
            ChatShare.owner_id == user.id,
        )
    )
    return {"share_id": share_id, "deleted": result.rowcount > 0}


@router.get("/{share_id}", response_model=SharePublicResponse, summary="公开读取分享的对话")
async def get_share(share_id: str, session: DbSession) -> SharePublicResponse:
    share = await session.scalar(select(ChatShare).where(ChatShare.share_id == share_id))
    if share is None:
        raise ShareNotFoundError()
    return SharePublicResponse(
        share_id=share.share_id,
        title=share.title,
        snapshot=share.snapshot or {},
        source_name=share.source_name,
        source_username=share.source_username,
        source_avatar=share.source_avatar,
        source_id=str(share.owner_id) if share.owner_id else None,
        created_at=share.created_at,
    )
