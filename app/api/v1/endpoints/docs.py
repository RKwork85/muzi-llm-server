"""muzidoc 文档/博客：当前用户自建文章的 CRUD。数据按 user_id 隔离。"""

from __future__ import annotations

import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import delete, func, select

from app.api.deps import get_authenticated_user, get_settings_dep
from app.core.config import Settings
from app.core.database.deps import DbSession
from app.core.exceptions import DocArticleNotFoundError, InvalidRequestError, UpstreamError
from app.data.oss_client import OssUploadError, oss_client_from_settings
from app.models.tables import DocArticle, User
from app.schemas.docs import (
    DocCreatedResponse,
    DocCreateRequest,
    DocDetailResponse,
    DocImageUploadResponse,
    DocListItem,
    DocListResponse,
    DocUpdateRequest,
)

router = APIRouter(prefix="/docs", tags=["docs"])
AuthenticatedUser = Annotated[User, Depends(get_authenticated_user)]
AppSettings = Annotated[Settings, Depends(get_settings_dep)]

# 正文插图：粘贴的多是截图，8MB 足够，也挡住误传的超大原图。
_MAX_IMAGE_BYTES = 8 * 1024 * 1024

# 只认文件头，不信 Content-Type：浏览器给的 type 可以被改写。
_IMAGE_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
    (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
    (b"GIF87a", ".gif", "image/gif"),
    (b"GIF89a", ".gif", "image/gif"),
)


def _detect_image(content: bytes) -> tuple[str, str] | None:
    """按文件头识别图片格式，返回 (扩展名, MIME)；不是受支持的图片返回 None。"""
    for signature, suffix, media in _IMAGE_SIGNATURES:
        if content.startswith(signature):
            return suffix, media
    # WebP 是 RIFF 容器：RIFF....WEBP
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return None


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
async def list_docs(
    session: DbSession,
    user: AuthenticatedUser,
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> DocListResponse:
    # 只取列表需要的列；摘要用 SQL 截取前 300 字符，避免把整篇正文加载进内存。
    stmt = (
        select(
            DocArticle.id,
            DocArticle.title,
            DocArticle.status,
            DocArticle.created_at,
            DocArticle.updated_at,
            func.left(DocArticle.content, 300).label("excerpt_src"),
        )
        .where(DocArticle.user_id == user.id)
        .order_by(DocArticle.updated_at.desc())
    )
    total = await session.scalar(
        select(func.count()).select_from(DocArticle).where(DocArticle.user_id == user.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit).offset(offset)
    rows = await session.execute(stmt)
    items = [
        DocListItem(
            id=row.id,
            title=row.title or "无标题文档",
            status=row.status,
            excerpt=_excerpt(row.excerpt_src or ""),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]
    return DocListResponse(items=items, total=total or 0)


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


@router.post(
    "/images",
    response_model=DocImageUploadResponse,
    summary="上传正文插图",
)
async def upload_doc_image(
    user: AuthenticatedUser,
    settings: AppSettings,
    file: UploadFile = File(...),
) -> DocImageUploadResponse:
    """接收编辑器里粘贴/拖入的图片，转存对象存储后返回可直接写进正文的地址。

    路径：`users/{uid}/image/doc/{YYYY-MM}/...`，与其他产物按用户归拢，
    但又和聊天/任务产物分开，方便按来源清理。
    """
    client = oss_client_from_settings(settings)
    if client is None:
        raise UpstreamError("图片存储未配置，暂时无法上传")

    content = await file.read(_MAX_IMAGE_BYTES + 1)
    if not content:
        raise InvalidRequestError("图片内容为空")
    if len(content) > _MAX_IMAGE_BYTES:
        raise InvalidRequestError("图片不能超过 8MB")

    detected = _detect_image(content)
    if detected is None:
        raise InvalidRequestError("仅支持 PNG、JPG、GIF、WebP 图片")
    suffix, media = detected

    # 文件名自己生成：粘贴的截图名字可能是 image.png 或纯空白，交给网关再清洗一次。
    filename = f"doc_image_{user.id.hex[:8]}{suffix}"
    try:
        urls = await client.upload_files(
            [(filename, content)],
            client.upload_path_for("doc-images"),
            user_id=str(user.id),
            category="image",
            scope="doc",
            content_types=[media],
        )
    except OssUploadError as exc:
        raise UpstreamError(exc.message) from exc

    url = urls[0]
    return DocImageUploadResponse(url=url, markdown=f"![图片]({url})")


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
