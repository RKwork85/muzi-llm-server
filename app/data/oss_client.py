"""调用本机 qs-oss 上传服务。不持有 OSS 密钥，也不接收本机绝对路径。"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

from app.core.config import Settings


class OssUploadError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class OssUploadClient:
    def __init__(self, base_url: str, *, timeout: float, prefix: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.prefix = prefix
        self._transport = transport

    def upload_path_for(self, task_id: str) -> str:
        return f"{self.prefix}/{task_id}"

    async def upload_files(self, files: list[tuple[str, bytes]], upload_path: str) -> list[str]:
        if not files:
            raise OssUploadError("OSS_UPLOAD_FAILED", "没有可上传的文件")
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self.timeout) as client:
                if len(files) == 1:
                    name, content = files[0]
                    response = await client.post(
                        f"{self.base_url}/upload/single",
                        params={"upload_path": upload_path},
                        files={"file": (name, content, "application/octet-stream")},
                    )
                    return [_one_url(response)]
                response = await client.post(
                    f"{self.base_url}/upload/multiple",
                    params={"upload_path": upload_path},
                    files=[("files", (name, content, "application/octet-stream")) for name, content in files],
                )
                return _many_urls(response, expected=len(files))
        except OssUploadError:
            raise
        except httpx.TimeoutException as exc:
            raise OssUploadError("OSS_UPLOAD_FAILED", "上传服务超时") from exc
        except httpx.HTTPError as exc:
            raise OssUploadError("OSS_UNAVAILABLE", "上传服务不可用") from exc


def oss_client_from_settings(settings: Settings) -> OssUploadClient | None:
    if not settings.oss_upload_base_url:
        return None
    return OssUploadClient(
        settings.oss_upload_base_url,
        timeout=settings.oss_upload_timeout_seconds,
        prefix=settings.oss_upload_prefix,
    )


def _one_url(response: httpx.Response) -> str:
    if response.status_code != 200:
        raise OssUploadError("OSS_UPLOAD_FAILED", "文件上传失败")
    try:
        body = response.json()
    except ValueError as exc:
        raise OssUploadError("OSS_UPLOAD_FAILED", "上传服务返回无效") from exc
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict) or data.get("success") is not True:
        raise OssUploadError("OSS_UPLOAD_FAILED", "文件上传失败")
    return _public_url(data.get("url"))


def _many_urls(response: httpx.Response, *, expected: int) -> list[str]:
    if response.status_code != 200:
        raise OssUploadError("OSS_UPLOAD_FAILED", "文件上传失败")
    try:
        body = response.json()
    except ValueError as exc:
        raise OssUploadError("OSS_UPLOAD_FAILED", "上传服务返回无效") from exc
    if not isinstance(body, dict) or body.get("failed_uploads"):
        raise OssUploadError("OSS_UPLOAD_FAILED", "文件上传失败")
    details = body.get("successful_uploads_detail")
    if not isinstance(details, list) or len(details) != expected:
        raise OssUploadError("OSS_UPLOAD_FAILED", "文件上传失败")
    urls: list[str] = []
    for item in details:
        if not isinstance(item, dict) or item.get("success") is not True:
            raise OssUploadError("OSS_UPLOAD_FAILED", "文件上传失败")
        urls.append(_public_url(item.get("url")))
    return urls


def _public_url(value: object) -> str:
    if not isinstance(value, str):
        raise OssUploadError("OSS_UPLOAD_FAILED", "上传服务没有返回可用地址")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise OssUploadError("OSS_UPLOAD_FAILED", "上传服务没有返回可用地址")
    return value
