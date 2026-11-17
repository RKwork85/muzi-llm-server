"""DeepSeek Files API 经服务端转发，密钥不暴露给浏览器。

对应 https://api.deepseek.com/files（OpenAI 兼容 Files API）：
- 图片（JPEG/PNG/GIF/WebP）与纯文本/表格/电子书用 purpose=user_data；
- PDF / Word / PowerPoint 等文档用 purpose=text_recognition 或 rich_layout_recognition。
  - text_recognition：只解析文字（纯文字文档更省 token / 图片额度）；
  - rich_layout_recognition：文字 + 文档中的图（需要结合图理解的文档）。

文档上传时不显式传 purpose 时，服务端会先判断文档里有没有图，自动选择解析方式：
含图 → rich_layout_recognition，纯文字 → text_recognition。
"""

from __future__ import annotations

import io
import os
import zipfile
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from app.api.deps import get_authenticated_user
from app.core.exceptions import ConfigurationError, InvalidRequestError, UpstreamError
from app.models.tables import User

router = APIRouter(prefix="/deepseek", tags=["deepseek"])

# DeepSeek Files API 允许的 purpose 取值。
_PURPOSES = {"user_data", "text_recognition", "rich_layout_recognition"}
# 文件名最大 512 字节。
_MAX_FILENAME_BYTES = 512
# 单文件上限：文档 100 MiB，图片 64 MiB，文本/表格/电子书 32 MiB；统一取最大 100 MiB。
_MAX_FILE_BYTES = 100 * 1024 * 1024
# 上传时限：文档 120s，其余 10 分钟；统一给足 180s。
_FILE_TIMEOUT_SECONDS = 180.0

# 图片本身即「含图」，作为文档解析时直接走 rich。
_IMAGE_EXTENSIONS = {".bmp", ".tif", ".tiff", ".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _settings(request: Request):
    settings = getattr(request.app.state, "settings", None)
    if settings is None or not str(getattr(settings, "deepseek_api_key", "")).strip():
        raise ConfigurationError("DeepSeek 密钥未配置")
    return settings


def _zip_has_entries(content: bytes, prefix: str) -> bool:
    """检查 OOXML（docx/pptx）压缩包指定目录下是否有实际文件。"""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            return any(
                name.startswith(prefix) and not name.endswith("/")
                for name in zf.namelist()
            )
    except (zipfile.BadZipFile, OSError):
        # 打不开视为含图，保守走 rich，避免漏掉图片信息。
        return True


def _pdf_has_images(content: bytes) -> bool:
    """用 PyMuPDF 检查 PDF 每一页是否引用了图片对象。"""
    try:
        import pymupdf  # PyMuPDF（新版本推荐入口）
    except ImportError:
        try:
            import fitz as pymupdf  # 旧版本别名
        except ImportError:
            # 未安装时保守走 rich。
            return True
    try:
        doc = pymupdf.open(stream=content, filetype="pdf")
        try:
            for page in doc:
                if page.get_images(full=True):
                    return True
            return False
        finally:
            doc.close()
    except Exception:
        # 解析失败保守走 rich。
        return True


def _document_has_images(filename: str, content: bytes) -> bool:
    """判断上传的文档是否包含图片，用于选择解析方式。"""
    ext = os.path.splitext(filename.lower())[1]
    if ext in _IMAGE_EXTENSIONS:
        return True
    if ext in {".docx", ".docm"}:
        return _zip_has_entries(content, "word/media/")
    if ext in {".pptx", ".pptm"}:
        return _zip_has_entries(content, "ppt/media/")
    if ext == ".pdf":
        return _pdf_has_images(content)
    # 老格式 .doc/.ppt 等无法可靠检测，保守走 rich。
    return True


def _auto_purpose(filename: str, content: bytes) -> str:
    """含图 → rich_layout_recognition，纯文字 → text_recognition。"""
    if _document_has_images(filename, content):
        return "rich_layout_recognition"
    return "text_recognition"


@router.post("/files", summary="经服务端上传 DeepSeek 文件（图片 / 文档）")
async def upload_file(
    request: Request,
    user: Annotated[User, Depends(get_authenticated_user)],
    file: UploadFile = File(...),
    purpose: str | None = Form(default=None),
) -> dict[str, Any]:
    settings = _settings(request)

    filename = file.filename or "upload.bin"
    if len(filename.encode("utf-8")) > _MAX_FILENAME_BYTES:
        raise InvalidRequestError("文件名过长")

    content = await file.read()
    if len(content) > _MAX_FILE_BYTES:
        raise InvalidRequestError("文件超过 100MB")

    # 文档不传 purpose 时自动判断解析方式；图片/文本等仍由前端显式传 user_data。
    if purpose is None:
        purpose = _auto_purpose(filename, content)

    if purpose not in _PURPOSES:
        raise InvalidRequestError("不支持的文件用途")

    url = f"{settings.deepseek_base_url.rstrip('/')}/files"
    headers = {"Authorization": f"Bearer {settings.deepseek_api_key.strip()}"}
    try:
        async with httpx.AsyncClient(timeout=_FILE_TIMEOUT_SECONDS) as client:
            response = await client.post(
                url,
                headers=headers,
                # purpose 必须放在 file 之前（DeepSeek 文档要求），httpx 按 data 先于 files 编码。
                data={"purpose": purpose},
                files={
                    "file": (
                        filename,
                        content,
                        file.content_type or "application/octet-stream",
                    )
                },
            )
    except httpx.HTTPError as exc:
        raise UpstreamError("DeepSeek 文件上传失败") from exc

    try:
        payload = response.json()
    except Exception as exc:
        raise UpstreamError("DeepSeek 文件响应无法解析") from exc

    if response.status_code >= 400:
        message = ""
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or "")
            elif isinstance(error, str):
                message = error
        raise UpstreamError(message or "DeepSeek 文件上传失败")

    file_id = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(file_id, str) or not file_id.strip():
        raise UpstreamError("DeepSeek 文件上传失败")
    return payload
