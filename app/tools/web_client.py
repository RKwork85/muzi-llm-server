"""DeepSeek Search / Fetch 客户端。抓取发生在 DeepSeek 服务器，不在本进程。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from contextvars import ContextVar
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import Settings

logger = logging.getLogger("qs_llm")

CURRENT_WEB_CLIENT: ContextVar["DeepSeekWebClient | None"] = ContextVar(
    "deepseek_web_client",
    default=None,
)
CURRENT_TOOL_BUDGET: ContextVar[float] = ContextVar("agent_tool_budget", default=30.0)

_RETRYABLE = frozenset({408, 429, 500, 504})
_CODES = {
    400: ("web_invalid_request", False),
    401: ("web_auth_error", False),
    402: ("web_balance_error", False),
    403: ("web_fetch_blocked", False),
    404: ("web_fetch_unavailable", False),
    408: ("web_timeout", True),
    413: ("web_invalid_request", False),
    429: ("web_rate_limited", True),
    500: ("web_upstream_error", True),
    504: ("web_timeout", True),
}


class WebCallError(Exception):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


_WEB_MESSAGES = {
    "web_invalid_request": "网页请求无效",
    "web_auth_error": "网页服务认证失败",
    "web_balance_error": "网页服务余额不足",
    "web_fetch_blocked": "网页抓取被拒绝",
    "web_fetch_unavailable": "没有可用的网页内容",
    "web_timeout": "网页请求超时",
    "web_rate_limited": "网页请求过于频繁",
    "web_upstream_error": "网页服务暂时不可用",
    "web_network_error": "网页服务连接失败",
    "web_bad_response": "网页服务返回了无法解析的响应",
}


def web_error_content(name: str, code: str, *, retryable: bool) -> str:
    return json.dumps(
        {
            "source": "server_tool",
            "name": name,
            "error": {"code": code, "message": _WEB_MESSAGES.get(code, "网页工具调用失败")},
            "retryable": retryable,
            "untrusted": True,
        },
        ensure_ascii=False,
    )


class SearchHit(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rerank_score: float = Field(ge=0, le=3)
    title: str
    url: str
    summary: str
    site_name: str | None = None
    date: str | None = None
    authority: int = Field(ge=-1, le=4)


class SearchPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    results: list[SearchHit]


class FetchPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = ""
    content: str


class DeepSeekWebClient:
    """trust_env 关闭，避免进程环境里的代理把 Authorization 送到别处。"""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def startup(self) -> None:
        if self._client is not None:
            return
        headers = {"Content-Type": "application/json"}
        key = self._settings.deepseek_api_key.strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        self._client = httpx.AsyncClient(
            base_url=self._settings.deepseek_base_url,
            timeout=httpx.Timeout(self._settings.deepseek_timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            transport=self._transport,
            headers=headers,
        )

    async def aclose(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()

    async def search(self, query: str, count: int, *, ori_question: str | None = None) -> list[dict[str, Any]]:
        body: dict[str, Any] = {"query": query, "count": count, "need_content": False}
        if ori_question:
            body["ori_question"] = ori_question[:128]
        payload = await self._post("/v1/tools/search", body)
        try:
            parsed = SearchPayload.model_validate(payload)
        except ValidationError as exc:
            raise WebCallError("web_bad_response") from exc
        hits: list[dict[str, Any]] = []
        for item in parsed.results:
            if not item.url.startswith(("http://", "https://")):
                continue
            hits.append(
                {
                    "title": item.title[:120],
                    "url": item.url,
                    "summary": item.summary[:500],
                    "site_name": item.site_name,
                    "date": item.date,
                    "authority": item.authority,
                    "rerank_score": item.rerank_score,
                }
            )
        return hits

    async def fetch(self, url: str) -> tuple[str, str]:
        payload = await self._post("/v1/tools/fetch", {"url": url})
        try:
            parsed = FetchPayload.model_validate(payload)
        except ValidationError as exc:
            raise WebCallError("web_bad_response") from exc
        return parsed.title, parsed.content

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        if self._client is None or not self._settings.has_api_key():
            raise WebCallError("web_auth_error")
        attempts = self._settings.web_tools_max_retries + 1
        budget = max(0.001, CURRENT_TOOL_BUDGET.get())
        host = urlsplit(str(body.get("url") or "")).hostname or "-"
        digest = hashlib.sha256(str(body.get("query") or "").encode("utf-8")).hexdigest()[:8]
        logger.info(
            "web call path=%s host=%s query_len=%s query_sha=%s",
            path,
            host,
            len(str(body.get("query") or "")),
            digest,
        )
        last: WebCallError | None = None
        for attempt in range(attempts):
            try:
                payload, status = await self._read(path, body, budget)
            except asyncio.CancelledError:
                raise
            except httpx.TimeoutException as exc:
                last = WebCallError("web_timeout", retryable=True)
                if not self._can_retry(last, attempt, attempts):
                    raise last from None
                await self._sleep(attempt)
                continue
            except httpx.TransportError:
                last = WebCallError("web_network_error")
                raise last from None
            if status == 200:
                return payload
            code, retryable = _CODES.get(status, ("web_upstream_error", status >= 500))
            last = WebCallError(code, retryable=retryable and status in _RETRYABLE)
            if not self._can_retry(last, attempt, attempts):
                raise last
            await self._sleep(attempt)
        raise last or WebCallError("web_upstream_error")

    async def _read(self, path: str, body: dict[str, Any], budget: float) -> tuple[Any, int]:
        assert self._client is not None
        timeout = httpx.Timeout(budget)
        async with self._client.stream("POST", path, json=body, timeout=timeout) as response:
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > self._settings.web_response_max_bytes:
                    raise WebCallError("web_bad_response")
                chunks.append(chunk)
            raw = b"".join(chunks)
            if response.status_code != 200:
                return None, response.status_code
            try:
                return httpx.Response(200, content=raw).json(), 200
            except ValueError as exc:
                raise WebCallError("web_bad_response") from exc

    def _can_retry(self, error: WebCallError, attempt: int, attempts: int) -> bool:
        return error.retryable and attempt + 1 < attempts and CURRENT_TOOL_BUDGET.get() > 0

    async def _sleep(self, attempt: int) -> None:
        delay = self._settings.web_tools_retry_backoff_seconds * (attempt + 1)
        if delay > 0:
            await asyncio.sleep(min(delay, CURRENT_TOOL_BUDGET.get()))
