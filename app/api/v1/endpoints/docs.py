"""muzidoc 文档/博客：当前用户自建文章的 CRUD。数据按 user_id 隔离。"""

from __future__ import annotations

import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import delete, select

from app.api.deps import get_authenticated_user
from app.core.database.deps import DbSession
from app.core.exceptions import DocArticleNotFoundError
from app.models.tables import DocArticle, User
from app.schemas.docs import (
    DocCreatedResponse,
    DocCreateRequest,
    DocDetailResponse,
    DocListItem,
    DocListResponse,
    DocUpdateRequest,
)

router = APIRouter(prefix="/docs", tags=["docs"])
AuthenticatedUser = Annotated[User, Depends(get_authenticated_user)]


def _excerpt(content: str, limit: int = 120) -> str:
    """从 markdown 正文提取无格式摘要，用于列表展示。"""
    text = content or ""
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[#>*_`~\-|]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def _to_detail(row: DocArticle) -> DocDetailResponse:
    return DocDetailResponse(
        id=row.id,
        title=row.title,
        content=row.content,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _get_owned(session: DbSession, user: User, article_id: UUID) -> DocArticle:
    row = await session.scalar(
        select(DocArticle).where(
            DocArticle.id == article_id,
            DocArticle.user_id == user.id,
        )
    )
    if row is None:
        raise DocArticleNotFoundError()
    return row


@router.get("", response_model=DocListResponse, summary="列出当前用户的文档")
async def list_docs(session: DbSession, user: AuthenticatedUser) -> DocListResponse:
    rows = list(
        await session.scalars(
            select(DocArticle)
            .where(DocArticle.user_id == user.id)
            .order_by(DocArticle.updated_at.desc())
        )
    )
    items = [
        DocListItem(
            id=row.id,
            title=row.title or "无标题文档",
            status=row.status,
            excerpt=_excerpt(row.content),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]
    return DocListResponse(items=items)


@router.post("", response_model=DocCreatedResponse, summary="新建文档")
async def create_doc(
    body: DocCreateRequest,
    session: DbSession,
    user: AuthenticatedUser,
) -> DocCreatedResponse:
    row = DocArticle(
        user_id=user.id,
        title=(body.title or "").strip()[:200],
        content=body.content or "",
        status=body.status,
    )
    session.add(row)
    await session.flush()
    return DocCreatedResponse(id=row.id)


@router.get("/{article_id}", response_model=DocDetailResponse, summary="读取一篇文档")
async def get_doc(
    article_id: UUID,
    session: DbSession,
    user: AuthenticatedUser,
) -> DocDetailResponse:
    row = await _get_owned(session, user, article_id)
    return _to_detail(row)


@router.patch("/{article_id}", response_model=DocDetailResponse, summary="更新文档")
async def update_doc(
    article_id: UUID,
    body: DocUpdateRequest,
    session: DbSession,
    user: AuthenticatedUser,
) -> DocDetailResponse:
    row = await _get_owned(session, user, article_id)
    if body.title is not None:
        row.title = body.title.strip()[:200]
    if body.content is not None:
        row.content = body.content
    if body.status is not None:
        row.status = body.status
    await session.flush()
    return _to_detail(row)


@router.delete("/{article_id}", summary="删除一篇文档")
async def delete_doc(
    article_id: UUID,
    session: DbSession,
    user: AuthenticatedUser,
) -> dict[str, object]:
    result = await session.execute(
        delete(DocArticle).where(
            DocArticle.id == article_id,
            DocArticle.user_id == user.id,
        )
    )
    return {"id": str(article_id), "deleted": result.rowcount > 0}
