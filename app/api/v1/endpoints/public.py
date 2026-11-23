"""不需要登录的公开配置与（登录后）可读的已出版内容。

前端在启动时要先知道「每个页面需要什么权限进入」，然后才能决定是放行还是跳登录页。
页面清单与匿名准入接口必须匿名可访问，所以只暴露权限码。

边界（改这里之前先看）：
- 文章是账号数据，`/public/docs/{id}` 也要登录：它只用于登录用户打开别人分享的
  已出版稿件，匿名访客一律 401。不存在「未登录可以浏览全平台文章」的入口。
- 只有 status='published' 的文章可读，草稿一律 404，连「存在但你看不到」都不透露。
- 作者只暴露 display_name / username，不返回 user_id 与邮箱。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.api.deps import get_authenticated_user
from app.authorization.application.authorization_service import AuthorizationService
from app.authorization.domain.catalog import SIDEBAR_PERMISSION_CODES
from app.authorization.domain.pages import PAGE_ACCESS_CODES, PAGE_CATALOG
from app.core.database.deps import DbSession
from app.core.exceptions import DocArticleNotFoundError
from app.models.rbac import Role
from app.models.tables import DocArticle, User
from app.schemas.docs import PublicDocDetailResponse
from app.schemas.rbac import AnonymousAccessOut, PageCatalogOut, PageOut
from app.services.doc_text import author_name

router = APIRouter(prefix="/public", tags=["public"])

AuthenticatedUser = Annotated[User, Depends(get_authenticated_user)]


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
                public=spec.public,
            )
            for spec in PAGE_CATALOG
        ]
    )


@router.get(
    "/anonymous-access",
    response_model=AnonymousAccessOut,
    summary="匿名访客的页面与侧边栏权限",
)
async def get_anonymous_access(session: DbSession) -> AnonymousAccessOut:
    """未登录访问者的页面准入与侧边栏显隐依据：数据库里 ``anonymous`` 角色的权限。

    前端路由守卫在「未检测到角色信息」（未登录）时读这份快照，据此判定
    该路由是放行还是跳登录——匿名访客不再写死，而是由后台对该角色的勾选决定。
    侧边栏入口显隐同样来自这份快照：``sidebar.*`` 码不在里面，游客端就会按
    「权限未加载」把全部入口画出来，后台取消勾选等于没生效。
    角色不存在或停用时返回空权限，前端只放行 ``meta.public`` 路由。

    只暴露页面浏览码（``*.access``）与侧边栏显隐码（``sidebar.*``），
    不带任何账号私有数据，也不透出写操作 / 管理类权限。
    """
    role = await session.scalar(select(Role).where(Role.code == "anonymous"))
    if role is None:
        return AnonymousAccessOut(active=False, permissions=[])
    if role.status != "active":
        return AnonymousAccessOut(role_name=role.name, active=False, permissions=[])
    granted = await AuthorizationService(session).role_permissions("anonymous")
    display_perms = sorted((PAGE_ACCESS_CODES | SIDEBAR_PERMISSION_CODES) & granted)
    return AnonymousAccessOut(role_name=role.name, active=True, permissions=display_perms)


@router.get(
    "/docs/{article_id}",
    response_model=PublicDocDetailResponse,
    summary="读取一篇已出版的文档",
)
async def get_public_doc(
    article_id: UUID,
    session: DbSession,
    _user: AuthenticatedUser,
) -> PublicDocDetailResponse:
    """登录用户读取一篇已出版的稿件（用于打开别人分享的链接）。

    曾经这里对匿名开放，结果访客的个人博客里能看到全平台的文章；文档是
    账号数据，未登录一律 401，别再把「published」当成「对所有人公开」。
    """
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
