"""聊天历史（规范化存储）接口：会话 + 消息两张表组织，含文字/图片/文件。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_authenticated_user
from app.core.database.deps import DbSession
from app.data.oss_client import OssUploadClient
from app.models.tables import User
from app.schemas.history import HistoryItem, HistoryListResponse, HistorySaveRequest
from app.services.history_service import HistoryService

router = APIRouter(prefix="/history", tags=["history"])
AuthenticatedUser = Annotated[User, Depends(get_authenticated_user)]


def _oss_client(request: Request) -> OssUploadClient | None:
    chat_service = getattr(request.app.state, "chat_service", None)
    return getattr(chat_service, "oss_client", None)


@router.get("", response_model=HistoryListResponse, summary="列出当前用户的聊天历史")
async def list_history(session: DbSession, user: AuthenticatedUser) -> HistoryListResponse:
    items = await HistoryService(session).list_items(user.id)
    return HistoryListResponse(
        items=[HistoryItem(**item) for item in items],
    )


@router.put("/{chat_id}", summary="保存或更新一段聊天历史快照")
async def save_history(
    chat_id: str,
    body: HistorySaveRequest,
    request: Request,
    session: DbSession,
    user: AuthenticatedUser,
) -> dict[str, object]:
    return await HistoryService(session).upsert(
        user.id,
        chat_id,
        body.title,
        body.snapshot,
        _oss_client(request),
    )


@router.delete("/{chat_id}", summary="删除一段聊天历史")
async def delete_history(
    chat_id: str,
    session: DbSession,
    user: AuthenticatedUser,
) -> dict[str, object]:
    deleted = await HistoryService(session).delete(user.id, chat_id)
    return {"chat_id": chat_id, "deleted": deleted}
