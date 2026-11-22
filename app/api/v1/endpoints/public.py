"""不需要登录的公开配置与公开内容。

前端在启动时要先知道「每个页面需要什么权限进入」，然后才能决定是放行还是跳登录页。
这些接口必须匿名可访问，所以只暴露权限码与已出版内容，不带任何账号私有数据。

边界（改这里之前先看）：
- 只有 status='published' 的文章会出现在公开接口里，草稿对匿名一律 404，
  连「存在但你看不到」都不透露。
- 作者只暴露 display_name / username，不返回 user_id 与邮箱。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.authorization.domain.pages import PAGE_CATALOG
from app.core.database.deps import DbSession
from app.core.exceptions import DocArticleNotFoundError
from app.models.tables import DocArticle, User
from app.schemas.docs import (
    PublicDocDetailResponse,
    PublicDocListItem,
    PublicDocListResponse,
)
from app.schemas.rbac import PageCatalogOut, PageOut
from app.services.doc_text import author_name, excerpt

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/pages", response_model=PageCatalogOut, summary="页面清单：进入条件与该页功能")
async def get_page_catalog() -> PageCatalogOut:
    """整份页面清单（公开）。

    前端路由守卫用它判定「这个路由要什么权限」，后台角色面板用它把权限
    按页面分组。两者都读同一份数据，新增页面时不需要改前端逻辑。
    """
    return PageCatalogOut(
        pages=[
            PageOut(
                key=spec.key,
                name=spec.name,
                path=spec.path,
                access=spec.access,
                features=list(spec.features),
            )
            for spec in PAGE_CATALOG
        ]
    )


@router.get("/docs", response_model=PublicDocListResponse, summary="列出所有已出版的文档")
async def list_public_docs(
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PublicDocListResponse:
    """全平台的公开文章（所有作者的已出版内容），按更新时间倒序。"""
    # 已停用账号的内容不再对外推荐；否则封禁一个作者后，他的文章还挂在公开列表上。
    visible = (
        DocArticle.status == "published",
        User.status == "active",
    )
    stmt = (
        select(
            DocArticle.id,
            DocArticle.title,
            DocArticle.status,
            DocArticle.created_at,
            DocArticle.updated_at,
            func.left(DocArticle.content, 300).label("excerpt_src"),
            User.display_name,
            User.username,
        )
        .join(User, User.id == DocArticle.user_id)
        .where(*visible)
        .order_by(DocArticle.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    total = await session.scalar(
        select(func.count())
        .select_from(DocArticle)
        .join(User, User.id == DocArticle.user_id)
        .where(*visible)
    )
    rows = await session.execute(stmt)
    items = [
        PublicDocListItem(
            id=row.id,
            title=row.title or "无标题文档",
            status=row.status,
            excerpt=excerpt(row.excerpt_src or ""),
            author=author_name(row.display_name, row.username),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]
    return PublicDocListResponse(items=items, total=total or 0)


@router.get(
    "/docs/{article_id}",
    response_model=PublicDocDetailResponse,
    summary="读取一篇已出版的文档",
)
async def get_public_doc(
    article_id: UUID,
    session: DbSession,
) -> PublicDocDetailResponse:
    row = (
        await session.execute(
            select(DocArticle, User.display_name, User.username)
            .join(User, User.id == DocArticle.user_id)
            .where(
                DocArticle.id == article_id,
                DocArticle.status == "published",
                User.status == "active",
            )
        )
    ).first()
    if row is None:
        # 草稿、他人私有稿、已停用作者的稿子统一 404：不区分「不存在」与「无权读」。
        raise DocArticleNotFoundError()
    article, display_name, username = row
    return PublicDocDetailResponse(
        id=article.id,
        title=article.title or "无标题文档",
        content=article.content,
        status=article.status,
        author=author_name(display_name, username),
        created_at=article.created_at,
        updated_at=article.updated_at,
    )
