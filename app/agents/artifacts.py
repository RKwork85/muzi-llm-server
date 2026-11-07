"""从工具信封里取出 artifact，并换成不含正文的摘要。"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError

_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_MAX_TITLE = 80


class _Incoming(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    title: str | None = Field(default=None, max_length=_MAX_TITLE)
    content: str = Field(min_length=1)
    content_type: str = "text/vnd.mermaid"
    source_tool: str | None = None


def absorb_tool_output(
    content: str,
    *,
    already: int,
    max_count: int,
    max_chars: int,
) -> tuple[str, list[dict[str, object]], str | None]:
    """返回替换后的工具内容、新 artifact、错误码。没有 artifact 时原样返回。"""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return content, [], None
    if not isinstance(payload, dict):
        return content, [], None
    inner, encoded = _inner_object(payload.get("result"))
    if inner is None or "artifacts" not in inner:
        return content, [], None
    raw_items = inner.get("artifacts")
    if not isinstance(raw_items, list) or not raw_items:
        return content, [], None
    if already + len(raw_items) > max_count:
        return _failure(payload, "ARTIFACT_LIMIT", "图表产物数量超出上限"), [], "ARTIFACT_LIMIT"
    accepted: list[dict[str, object]] = []
    total = 0
    for item in raw_items:
        try:
            parsed = _Incoming.model_validate(item)
        except ValidationError:
            return _failure(payload, "ARTIFACT_LIMIT", "图表产物无效"), [], "ARTIFACT_LIMIT"
        if parsed.type == "mermaid":
            if parsed.content_type != "text/vnd.mermaid":
                return _failure(payload, "ARTIFACT_LIMIT", "图表产物无效"), [], "ARTIFACT_LIMIT"
        elif parsed.type in {"file", "package"}:
            if parsed.content_type != "application/vnd.qs.file+json":
                return _failure(payload, "ARTIFACT_LIMIT", "文件产物无效"), [], "ARTIFACT_LIMIT"
        else:
            return _failure(payload, "ARTIFACT_LIMIT", "图表产物无效"), [], "ARTIFACT_LIMIT"
        if len(parsed.content) > max_chars or total + len(parsed.content) > max_count * max_chars:
            return _failure(payload, "ARTIFACT_LIMIT", "图表产物超出长度上限"), [], "ARTIFACT_LIMIT"
        total += len(parsed.content)
        artifact_id = f"art_{already + len(accepted) + 1}"
        source = parsed.source_tool or str(payload.get("name") or "tool")
        if _TOOL_NAME.fullmatch(source) is None:
            source = "tool"
        accepted.append(
            {
                "id": artifact_id,
                "type": parsed.type,
                "title": parsed.title,
                "content": parsed.content,
                "content_type": parsed.content_type,
                "source_tool": source,
            }
        )
    summaries = [
        {"id": item["id"], "type": item["type"], "size": len(str(item["content"]))}
        for item in accepted
    ]
    inner.pop("mermaid", None)
    inner["artifacts"] = summaries
    inner["artifact_ids"] = [item["id"] for item in accepted]
    payload["result"] = json.dumps(inner, ensure_ascii=False) if encoded else inner
    return json.dumps(payload, ensure_ascii=False), accepted, None


def _inner_object(result: object) -> tuple[dict[str, object] | None, bool]:
    if isinstance(result, dict):
        return result, False
    if not isinstance(result, str):
        return None, False
    try:
        parsed = json.loads(result)
    except json.JSONDecodeError:
        return None, False
    if not isinstance(parsed, dict):
        return None, False
    return parsed, True


def _failure(payload: dict[str, object], code: str, message: str) -> str:
    name = payload.get("name")
    body: dict[str, object] = {
        "source": "server_tool",
        "error": {"code": code, "message": message},
    }
    if isinstance(name, str):
        body["name"] = name
    return json.dumps(body, ensure_ascii=False)
