"""聊天历史规范化存储：把整份会话拆成会话行 + 消息行落库，读取时重组。

数据组织：
- chat_history_sessions：会话元数据（标题、时间、模型、置顶、摘要、关键词等）
- chat_history_messages：逐条消息（文字正文、多模态 content 块、生成图片、下载文件、
  工具运行、搜索、规划字段分别落列，保证文字/图片/文件全部保留且可检索）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InvalidRequestError
from app.data.oss_client import OssUploadClient
from app.models.tables import ChatHistoryMessage, ChatHistorySession

logger = logging.getLogger("muzi_llm")

_IMAGE_CONTENT_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

# 消息里额外落进 planning JSONB 的字段（其余字段走显式列）。
_PLANNING_KEYS = (
    "generating_images",
    "gen_image_error",
    "gen_image_meta",
    "gen_image_started_at",
    "gen_image_duration_sec",
    "interaction",
    "plan",
    "run",
    "approved_revision",
    "plan_id",
    "interaction_error",
    "planning_request",
    "interaction_value",
    "interaction_submission_id",
    "interaction_submission_key",
)


def _is_ark_url(value: str) -> bool:
    if not value or not value.startswith(("http://", "https://")):
        return False
    try:
        host = (urlparse(value).hostname or "").lower()
    except ValueError:
        return False
    return host == "volces.com" or host.endswith(".volces.com")


def _walk_urls(node: Any, found: list[str]) -> None:
    """收集快照里所有字符串 URL（递归，覆盖 content 块 / generated_images / downloads 等）。"""
    if isinstance(node, dict):
        for value in node.values():
            _walk_urls(value, found)
    elif isinstance(node, list):
        for value in node:
            _walk_urls(value, found)
    elif isinstance(node, str) and _is_ark_url(node):
        found.append(node)


def _rewrite_urls(node: Any, mapping: dict[str, str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str) and value in mapping:
                node[key] = mapping[value]
            else:
                _rewrite_urls(value, mapping)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if isinstance(value, str) and value in mapping:
                node[index] = mapping[value]
            else:
                _rewrite_urls(value, mapping)


async def persist_ark_images(snapshot: dict[str, Any], oss_client: OssUploadClient | None, chat_id: str = "") -> None:
    """把快照里的方舟临时图片下载并转存 OSS，替换为永久 URL（尽力而为）。"""
    if not isinstance(snapshot, dict) or oss_client is None:
        return
    urls: list[str] = []
    _walk_urls(snapshot, urls)
    unique = list(dict.fromkeys(urls))
    if not unique:
        return

    upload_path = oss_client.upload_path_for(f"chat-history/{chat_id}" if chat_id else "chat-history")
    mapping: dict[str, str] = {}
    transport = httpx.AsyncHTTPTransport()
    async with httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(15.0), follow_redirects=True) as client:
        for url in unique:
            try:
                response = await client.get(url)
                response.raise_for_status()
                suffix = _IMAGE_CONTENT_TYPES.get(
                    (response.headers.get("content-type") or "").split(";")[0].strip().lower(),
                    ".png",
                )
                uploaded = await oss_client.upload_files(
                    [(f"chat_image_{uuid4().hex[:12]}{suffix}", response.content)],
                    upload_path,
                )
                if uploaded and uploaded[0]:
                    mapping[url] = uploaded[0]
            except Exception as exc:  # noqa: BLE001 — 单张失败不阻断整体保存
                logger.warning("history image persist failed url_host=%s error=%s", urlparse(url).hostname, type(exc).__name__)

    if mapping:
        _rewrite_urls(snapshot, mapping)


def _content_text(content: Any) -> str | None:
    """从 content（str / 多模态块数组）提取纯文本正文，供列表展示与检索。"""
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    text = "\n".join(parts).strip()
    return text or None


def _to_millis(value: Any) -> int | None:
    """把时间字段归一化为整数毫秒时间戳。

    历史快照可能来自旧版本地存储：createdAt / thinking_started_at 既可能是
    Date.now() 毫秒数，也可能是 ISO 字符串。BigInteger 列只接受整数。
    兼容：整数毫秒、秒级时间戳、纯数字字符串、ISO 8601 字符串。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        ms = int(value)
        return ms * 1000 if 0 < ms < 1_000_000_000_000 else ms
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.lstrip("-").isdigit():
            ms = int(text)
            return ms * 1000 if 0 < ms < 1_000_000_000_000 else ms
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except ValueError:
            return None
    return None


def _split_messages(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    raw = snapshot.get("chatMessages")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for index, message in enumerate(raw):
        if not isinstance(message, dict):
            continue
        planning = {key: message[key] for key in _PLANNING_KEYS if key in message}
        out.append(
            {
                "seq": index,
                "role": str(message.get("role") or "user"),
                "content": message.get("content") if "content" in message else {},
                "content_text": _content_text(message.get("content")),
                "reasoning_content": message.get("reasoning_content"),
                "thinking_duration_sec": message.get("thinking_duration_sec"),
                "thinking_started_at": _to_millis(message.get("thinking_started_at")),
                "usage": message.get("usage") if isinstance(message.get("usage"), dict) else None,
                "generated_images": message.get("generated_images") if isinstance(message.get("generated_images"), list) else [],
                "downloads": message.get("downloads") if isinstance(message.get("downloads"), list) else [],
                "tool_runs": message.get("tool_runs") if isinstance(message.get("tool_runs"), list) else [],
                "search_results": message.get("search_results") if isinstance(message.get("search_results"), list) else [],
                "search_query": message.get("search_query"),
                "search_error": message.get("search_error"),
                "trace": message.get("trace") if isinstance(message.get("trace"), list) else [],
                "planning": planning or None,
                "created_at_ms": _to_millis(message.get("createdAt")),
            }
        )
    return out


def _assemble_message(row: ChatHistoryMessage) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": row.role,
        "content": row.content,
    }
    if row.content_text is not None:
        message["content_text"] = row.content_text
    if row.reasoning_content:
        message["reasoning_content"] = row.reasoning_content
    if row.thinking_duration_sec is not None:
        message["thinking_duration_sec"] = row.thinking_duration_sec
    if row.thinking_started_at is not None:
        message["thinking_started_at"] = row.thinking_started_at
    if isinstance(row.usage, dict):
        message["usage"] = row.usage
    if row.generated_images:
        message["generated_images"] = row.generated_images
    if row.downloads:
        message["downloads"] = row.downloads
    if row.tool_runs:
        message["tool_runs"] = row.tool_runs
    if row.search_results:
        message["search_results"] = row.search_results
    if row.search_query:
        message["search_query"] = row.search_query
    if row.search_error:
        message["search_error"] = row.search_error
    if row.trace:
        message["trace"] = row.trace
    if isinstance(row.planning, dict) and row.planning:
        message.update(row.planning)
    if row.created_at_ms is not None:
        message["createdAt"] = row.created_at_ms
    return message


def _assemble_session(row: ChatHistorySession, messages: list[ChatHistoryMessage]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "chatId": row.chat_id,
        "chatTitle": row.chat_title,
        "chatMessages": [_assemble_message(m) for m in messages],
    }
    if row.chat_time:
        snapshot["chatTime"] = row.chat_time
    if row.mode:
        snapshot["mode"] = row.mode
    if row.model_id:
        snapshot["modelId"] = row.model_id
    if row.model_name:
        snapshot["modelName"] = row.model_name
    if row.provider_id:
        snapshot["providerId"] = row.provider_id
    if row.model_category:
        snapshot["modelCategory"] = row.model_category
    if row.model_api:
        snapshot["modelApi"] = row.model_api
    snapshot["pinned"] = bool(row.pinned)
    if row.pinned_at is not None:
        snapshot["pinnedAt"] = row.pinned_at
    if row.server_conversation_id:
        snapshot["serverConversationId"] = row.server_conversation_id
    if row.chat_summary:
        snapshot["chatSummary"] = row.chat_summary
    if row.chat_keywords:
        snapshot["chatKeywords"] = row.chat_keywords
    snapshot.update(row.extra or {})
    return snapshot


class HistoryService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_items(self, owner_id: Any) -> list[dict[str, Any]]:
        sessions = list(
            await self._session.scalars(
                select(ChatHistorySession)
                .where(ChatHistorySession.user_id == owner_id)
                .order_by(ChatHistorySession.updated_at.desc())
            )
        )
        items: list[dict[str, Any]] = []
        for row in sessions:
            messages = list(
                await self._session.scalars(
                    select(ChatHistoryMessage)
                    .where(ChatHistoryMessage.session_id == row.id)
                    .order_by(ChatHistoryMessage.seq)
                )
            )
            items.append(
                {
                    "chat_id": row.chat_id,
                    "title": row.chat_title,
                    "snapshot": _assemble_session(row, messages),
                    "updated_at": row.updated_at,
                }
            )
        return items

    async def upsert(
        self,
        owner_id: Any,
        chat_id: str,
        title: str,
        snapshot: dict[str, Any],
        oss_client: OssUploadClient | None,
    ) -> dict[str, Any]:
        if not chat_id or len(chat_id) > 64:
            raise InvalidRequestError("chat_id 缺失或过长")
        if not isinstance(snapshot, dict):
            raise InvalidRequestError("snapshot 必须是对象")

        messages = _split_messages(snapshot)
        has_user = any(m["role"] == "user" for m in messages)
        if not has_user:
            # 空会话（无用户发言）不落库，避免堆积空壳
            await self.delete(owner_id, chat_id)
            return {"chat_id": chat_id, "deleted": True}

        await persist_ark_images(snapshot, oss_client, chat_id)
        # 图片转存会改 content/generated_images，重新拆解一次以落最新 URL
        messages = _split_messages(snapshot)

        row = await self._session.scalar(
            select(ChatHistorySession).where(
                ChatHistorySession.user_id == owner_id,
                ChatHistorySession.chat_id == chat_id,
            )
        )
        if row is None:
            row = ChatHistorySession(user_id=owner_id, chat_id=chat_id)
            self._session.add(row)
            await self._session.flush()
        else:
            # 整段覆盖：先清空旧消息，再写入新消息
            await self._session.execute(
                delete(ChatHistoryMessage).where(ChatHistoryMessage.session_id == row.id)
            )

        row.chat_title = (title or snapshot.get("chatTitle") or "")[:200]
        row.chat_time = snapshot.get("chatTime")
        row.mode = snapshot.get("mode")
        row.model_id = snapshot.get("modelId")
        row.model_name = snapshot.get("modelName")
        row.provider_id = snapshot.get("providerId")
        row.model_category = snapshot.get("modelCategory")
        row.model_api = snapshot.get("modelApi")
        row.pinned = bool(snapshot.get("pinned"))
        row.pinned_at = snapshot.get("pinnedAt")
        row.server_conversation_id = snapshot.get("serverConversationId")
        row.chat_summary = snapshot.get("chatSummary")
        row.chat_keywords = snapshot.get("chatKeywords") if isinstance(snapshot.get("chatKeywords"), list) else []
        row.extra = {k: v for k, v in snapshot.items() if k not in _SESSION_KEYS and k != "chatMessages"}

        for item in messages:
            self._session.add(
                ChatHistoryMessage(
                    session_id=row.id,
                    seq=item["seq"],
                    role=item["role"],
                    content=item["content"],
                    content_text=item["content_text"],
                    reasoning_content=item["reasoning_content"],
                    thinking_duration_sec=item["thinking_duration_sec"],
                    thinking_started_at=item["thinking_started_at"],
                    usage=item["usage"],
                    generated_images=item["generated_images"],
                    downloads=item["downloads"],
                    tool_runs=item["tool_runs"],
                    search_results=item["search_results"],
                    search_query=item["search_query"],
                    search_error=item["search_error"],
                    trace=item["trace"],
                    planning=item["planning"],
                    created_at_ms=item["created_at_ms"],
                )
            )

        await self._session.flush()
        return {"chat_id": chat_id, "updated_at": row.updated_at}

    async def delete(self, owner_id: Any, chat_id: str) -> bool:
        result = await self._session.execute(
            delete(ChatHistorySession).where(
                ChatHistorySession.user_id == owner_id,
                ChatHistorySession.chat_id == chat_id,
            )
        )
        return result.rowcount > 0


_SESSION_KEYS = {
    "chatId",
    "chatTitle",
    "chatTime",
    "mode",
    "modelId",
    "modelName",
    "providerId",
    "modelCategory",
    "modelApi",
    "pinned",
    "pinnedAt",
    "serverConversationId",
    "chatSummary",
    "chatKeywords",
}
