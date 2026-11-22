"""调用本机 muzi-oss 上传服务。不持有 OSS 密钥，也不接收本机绝对路径。"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from app.core.config import Settings

# 网关只接受 UUID 形式的 user_id。上游偶尔会给空值或非 UUID 标识（匿名、
# 测试替身），这时不能把脏值发过去，退化成旧布局比上传失败更好。
_USER_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$"
)


class OssUploadError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class OssUploadClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float,
        prefix: str,
        token: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.prefix = prefix
        # 网关开了 API_TOKEN 时必须带上，否则写接口一律 401。
        self.token = token.strip()
        self._transport = transport

    def upload_path_for(self, task_id: str) -> str:
        """旧布局前缀。新代码优先用 user_id + category，这里只作兜底。"""
        return f"{self.prefix}/{task_id}"

    def _key_of(self, url: str) -> str:
        """公网地址 → 对象键。网关的读路径固定是 ``/files/<key>``。"""
        parsed = urlparse(url)
        marker = "/files/"
        index = parsed.path.find(marker)
        if index < 0:
            raise OssUploadError("OSS_UPLOAD_FAILED", "图片地址不是本服务签发的")
        key = parsed.path[index + len(marker) :].strip("/")
        if not key or ".." in key:
            raise OssUploadError("OSS_UPLOAD_FAILED", "图片地址无效")
        return key

    async def delete_url(self, url: str) -> bool:
        """删除一张已上传的图。对象不存在也视为成功（回收任务要幂等）。"""
        key = self._key_of(url)
        headers = {"X-Internal-Token": self.token} if self.token else {}
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self.timeout, trust_env=False
            ) as client:
                response = await client.delete(
                    f"{self.base_url}/files/{key}", headers=headers
                )
        except httpx.TimeoutException as exc:
            raise OssUploadError("OSS_UPLOAD_FAILED", "删除服务超时") from exc
        except httpx.HTTPError as exc:
            raise OssUploadError("OSS_UNAVAILABLE", "删除服务不可用") from exc
        if response.status_code in (200, 204, 404):
            return True
        raise OssUploadError("OSS_UPLOAD_FAILED", "删除图片失败")

    async def upload_files(
        self,
        files: list[tuple[str, bytes]],
        upload_path: str = "",
        *,
        user_id: str | None = None,
        category: str | None = None,
        scope: str | None = None,
        content_types: list[str] | None = None,
    ) -> list[str]:
        """上传并返回公网地址。

        寻址优先级：`user_id + category`（新布局 `users/{uid}/{cat}/...`）> `upload_path`（旧布局）。
        布局规则只在 muzi-oss 里定义一次，本服务只传语义参数。
        `content_types` 与 `files` 一一对应；不传时沿用 octet-stream（qs-oss 的历史行为）。
        """
        if not files:
            raise OssUploadError("OSS_UPLOAD_FAILED", "没有可上传的文件")
        media_types = list(content_types or [])
        if media_types and len(media_types) != len(files):
            raise OssUploadError("OSS_UPLOAD_FAILED", "文件与类型数量不一致")
        params: dict[str, str] = {}
        if user_id and category and _USER_ID_RE.match(user_id):
            params["user_id"] = user_id
            params["category"] = category
            if scope:
                params["scope"] = scope
        elif upload_path:
            params["upload_path"] = upload_path
        try:
            headers = {"X-Internal-Token": self.token} if self.token else {}
            async with httpx.AsyncClient(transport=self._transport, timeout=self.timeout, trust_env=False) as client:
                if len(files) == 1:
                    name, content = files[0]
                    media = media_types[0] if media_types else "application/octet-stream"
                    response = await client.post(
                        f"{self.base_url}/upload/single",
                        params=params,
                        headers=headers,
                        files={"file": (name, content, media)},
                    )
                    return [_one_url(response)]
                response = await client.post(
                    f"{self.base_url}/upload/multiple",
                    params=params,
                    headers=headers,
                    files=[
                        (
                            "files",
                            (name, content, media_types[i] if media_types else "application/octet-stream"),
                        )
                        for i, (name, content) in enumerate(files)
                    ],
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
        token=settings.oss_upload_token,
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
