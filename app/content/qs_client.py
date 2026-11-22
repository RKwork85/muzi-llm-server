"""调用本机 qs-datacapture 直调采集服务。

qs-datacapture 已改造为常驻 HTTP 服务，后端（本服务）作为调度中心，
直调其 ``POST /api/collect/run`` 端点：把 ``user_id`` / ``task_id`` 透传下去，
采集服务内部完成 Playwright 采集 + 媒体直传 muzi-oss，返回 ``preview_data``。

与 ``app/data/oss_client.py`` 一样：不持有采集平台的 Cookie / 密钥，
凭证只落在 qs-datacapture 服务端（``auth/`` 目录）与 muzi-oss 服务端。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


class QSDatacaptureError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class QSDatacaptureClient:
    """qs-datacapture 直调采集客户端。登录 token 在进程内缓存复用。"""

    def __init__(
        self,
        base_url: str,
        *,
        username: str,
        password: str,
        timeout: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self._transport = transport
        self._token: str = ""

    async def _ensure_token(self, client: httpx.AsyncClient) -> str:
        if self._token:
            return self._token
        try:
            resp = await client.post(
                f"{self.base_url}/api/auth/login",
                json={"username": self.username, "password": self.password},
            )
        except httpx.HTTPError as exc:
            raise QSDatacaptureError("QS_UNAVAILABLE", "采集服务不可用") from exc
        if resp.status_code != 200:
            raise QSDatacaptureError("QS_AUTH_FAILED", "采集服务登录失败")
        body = resp.json()
        token = (body or {}).get("token") if isinstance(body, dict) else None
        if not token:
            raise QSDatacaptureError("QS_AUTH_FAILED", "采集服务未返回 token")
        self._token = str(token)
        return self._token

    async def collect(
        self,
        type_: str,
        params: dict[str, Any],
        *,
        user_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        """直调采集，返回采集服务 result（含 preview_data）。

        ``params`` 里可含 ``urls`` / ``maxNotes`` 等，``user_id`` / ``task_id``
        由这里强制注入，避免调用方漏传导致媒体无法归到正确用户目录。
        """
        merged_params: dict[str, Any] = dict(params or {})
        merged_params["user_id"] = user_id
        merged_params["task_id"] = task_id

        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self.timeout, trust_env=False
            ) as client:
                token = await self._ensure_token(client)
                resp = await client.post(
                    f"{self.base_url}/api/collect/run",
                    json={"type": type_, "params": merged_params},
                    headers={"Authorization": f"Bearer {token}"},
                )
        except QSDatacaptureError:
            raise
        except httpx.TimeoutException as exc:
            raise QSDatacaptureError("QS_TIMEOUT", "采集服务响应超时") from exc
        except httpx.HTTPError as exc:
            raise QSDatacaptureError("QS_UNAVAILABLE", "采集服务不可用") from exc

        if resp.status_code != 200:
            raise QSDatacaptureError(
                "QS_COLLECT_FAILED", f"采集失败: HTTP {resp.status_code}"
            )
        try:
            body = resp.json()
        except ValueError as exc:
            raise QSDatacaptureError("QS_COLLECT_FAILED", "采集服务返回无效") from exc
        if not isinstance(body, dict) or body.get("ok") is not True:
            error = body.get("error") if isinstance(body, dict) else None
            raise QSDatacaptureError(
                "QS_COLLECT_FAILED", str(error or "采集失败")
            )
        result = body.get("result")
        if not isinstance(result, dict):
            raise QSDatacaptureError("QS_COLLECT_FAILED", "采集服务未返回结果")
        return result


def qs_client_from_settings(settings: Settings) -> QSDatacaptureClient | None:
    if not settings.qs_datacapture_base_url:
        return None
    return QSDatacaptureClient(
        settings.qs_datacapture_base_url,
        username=settings.qs_datacapture_username,
        password=settings.qs_datacapture_password,
        timeout=settings.qs_datacapture_timeout_seconds,
    )
