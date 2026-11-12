"""把已结算的额度记录按轮次读出来。

这里只做查询和聚合，不调用 settle，也不改 used_value。
摘要没有独立 Turn ID 时，只按同一会话、时间先后关联，不按问题文本合并。
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.metering import (
    LlmUsageBreakdown,
    LlmUsageRecord,
    QuotaTransaction,
    SkillUsageRecord,
    SummaryUpdate,
    ToolUsageRecord,
)
from app.models.rbac import UsageRecord
from app.models.tables import ChatHistoryMessage, ChatHistorySession, ConversationMessage, ConversationSession

SHANGHAI = timezone(timedelta(hours=8))
SUMMARY_WINDOW = timedelta(hours=6)
RECORD_LIMIT = 1000
MESSAGE_LIMIT = 5000

INPUT_CATEGORIES = (
    ("system", "系统提示词"),
    ("developer", "开发者提示词"),
    ("tool_schema", "工具定义"),
    ("skill_instruction", "Skill 注入"),
    ("user", "当前用户输入"),
    ("history", "历史消息"),
    ("tool_result", "工具结果"),
    ("other_input", "未归因输入"),
)
OUTPUT_CATEGORIES = (
    ("reasoning", "思考 Token"),
    ("answer", "最终回答"),
    ("tool_call", "工具调用参数"),
    ("other_output", "其他输出"),
)
EXTRA_LABELS = {
    "retrieval": "检索上下文",
    "file": "文件内容",
    "image": "图片输入",
    "audio": "音频",
    "video": "视频",
    "structured_output": "结构化输出",
}
ROLE_LABELS = {
    "primary": "主模型调用",
    "summary": "上下文摘要",
    "other": "其他模型调用",
}
CATEGORY_LABELS = {
    **dict(INPUT_CATEGORIES),
    **dict(OUTPUT_CATEGORIES),
    **EXTRA_LABELS,
    "cache_read": "缓存读入",
    "cache_write": "缓存写入",
    "uncached_input": "未缓存输入",
}
PROVIDER_LABELS = {"ark": "方舟", "deepseek": "DeepSeek"}


def mask_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) <= 8:
        return text[:2] + "****"
    return f"{text[:4]}…{text[-4:]}"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any, default: int = 0) -> int:
    parsed = _int_or_none(value)
    return default if parsed is None else parsed


def _snippet(text: str, limit: int = 80) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _conv_key(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return str(uuid.UUID(text))
    except (ValueError, AttributeError):
        return text


def _is_summary(record: dict[str, Any]) -> bool:
    context = record.get("context") or {}
    return record.get("source") == "summary" or context.get("purpose") == "summary"


def _kind(record: dict[str, Any]) -> str:
    metric = record.get("metric") or "chat"
    return metric if metric in {"chat", "image"} else "chat"


def _figure(value: int | None, quality: str) -> dict[str, Any]:
    if quality == "unavailable" or value is None:
        return {"value": None, "quality": "unavailable"}
    return {"value": int(value), "quality": quality}


def _sum_figures(figures: list[dict[str, Any]]) -> dict[str, Any]:
    if not figures or any(item.get("quality") != "actual" or item.get("value") is None for item in figures):
        return _figure(None, "unavailable")
    return _figure(sum(int(item["value"]) for item in figures), "actual")


def _num(figure: dict[str, Any]) -> int:
    if figure.get("quality") == "unavailable" or figure.get("value") is None:
        return 0
    return int(figure["value"])


def _duration_ms(started: datetime | None, completed: datetime | None) -> int | None:
    if started is None or completed is None:
        return None
    delta = int((_aware(completed) - _aware(started)).total_seconds() * 1000)
    return delta if delta >= 0 else None


def _bucket_key(record: dict[str, Any]) -> tuple[str, str]:
    if _is_summary(record):
        return ("summary", str(record["id"]))
    key = str(record.get("settlement_key") or record["id"])
    if ":" in key:
        head, tail = key.rsplit(":", 1)
        if head and (tail == "rest" or tail.isdigit()):
            return ("run", head)
    return ("single", key)


def _conversation_id(records: list[dict[str, Any]]) -> str | None:
    ordered = [row for row in records if not _is_summary(row)] or records
    for row in ordered:
        key = _conv_key((row.get("context") or {}).get("conversation_id"))
        if key:
            return key
    return None


def _title(records: list[dict[str, Any]]) -> str | None:
    for row in records:
        title = (row.get("context") or {}).get("conversation_title")
        if title:
            return str(title)[:200]
    return None


def _turn_time(records: list[dict[str, Any]]) -> datetime:
    primary = [row for row in records if not _is_summary(row)] or records
    return min(_aware(row["created_at"]) for row in primary)


def _ledger_figure(records: list[dict[str, Any]], field: str) -> dict[str, Any]:
    total = 0
    for row in records:
        context = row.get("context") or {}
        if field not in context or context.get(field) is None:
            return _figure(None, "unavailable")
        total += _int(context.get(field))
    return _figure(total, "actual")


def assign_llm(records: list[dict[str, Any]], llm_records: list[dict[str, Any]]) -> None:
    exact_keys = {row.get("settlement_key") for row in records if row.get("settlement_key")}
    by_exact: dict[str, list[dict[str, Any]]] = defaultdict(list)
    extras: list[dict[str, Any]] = []
    for row in llm_records:
        key = row.get("settlement_key") or ""
        if key in exact_keys:
            by_exact[key].append(row)
        else:
            extras.append(row)
    used: set[str] = set()
    for record in records:
        key = record.get("settlement_key") or ""
        matched = list(by_exact.get(key, []))
        for row in extras:
            row_id = str(row.get("id"))
            if row_id in used:
                continue
            settlement = row.get("settlement_key") or ""
            if key and settlement.startswith(key + ":") and settlement not in exact_keys:
                matched.append(row)
                used.add(row_id)
        record["llm"] = matched


def _call_from_llm(row: dict[str, Any], summary: bool) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "request_id": row.get("request_id"),
        "provider": row.get("provider"),
        "model": row.get("model"),
        "runtime_mode": row.get("runtime_mode"),
        "usage_source": row.get("usage_source") or "chat",
        "status": row.get("status") or "completed",
        "started_at": row.get("started_at"),
        "completed_at": row.get("completed_at"),
        "created_at": _aware(row["created_at"]),
        "origin": "provider",
        "role": "summary" if summary or row.get("usage_source") == "summary" else None,
        "input_tokens": _figure(row.get("input_tokens"), "actual" if row.get("input_tokens") is not None else "unavailable"),
        "output_tokens": _figure(row.get("output_tokens"), "actual" if row.get("output_tokens") is not None else "unavailable"),
        "total_tokens": _figure(row.get("total_tokens"), "actual" if row.get("total_tokens") is not None else "unavailable"),
        "reasoning_tokens": _figure(
            row.get("reasoning_tokens"),
            "actual" if row.get("reasoning_tokens") is not None else "unavailable",
        ),
        "cache_read_tokens": _figure(
            row.get("cache_read_tokens"),
            "actual" if row.get("cache_read_tokens") is not None else "unavailable",
        ),
        "cache_write_tokens": _figure(
            row.get("cache_write_tokens"),
            "actual" if row.get("cache_write_tokens") is not None else "unavailable",
        ),
        "breakdown": list(row.get("breakdown") or []),
    }


def _call_from_record(record: dict[str, Any], summary: bool) -> dict[str, Any]:
    context = record.get("context") or {}
    prompt = _int_or_none(context.get("prompt_tokens")) if "prompt_tokens" in context else None
    completion = _int_or_none(context.get("completion_tokens")) if "completion_tokens" in context else None
    return {
        "id": str(record["id"]),
        "request_id": None,
        "provider": None,
        "model": context.get("model"),
        "runtime_mode": None,
        "usage_source": record.get("source") or "chat",
        "status": record.get("result") or "settled",
        "started_at": None,
        "completed_at": record.get("created_at"),
        "created_at": _aware(record["created_at"]),
        "origin": "ledger",
        "role": "summary" if summary else None,
        "input_tokens": _figure(prompt, "actual" if prompt is not None else "unavailable"),
        "output_tokens": _figure(completion, "actual" if completion is not None else "unavailable"),
        "total_tokens": _figure(_int(record.get("amount")), "actual"),
        "reasoning_tokens": _figure(None, "unavailable"),
        "cache_read_tokens": _figure(None, "unavailable"),
        "cache_write_tokens": _figure(None, "unavailable"),
        "breakdown": [],
    }


def _calls_for(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda row: _aware(row["created_at"])):
        summary = _is_summary(record)
        linked = record.get("llm") or []
        if not linked:
            calls.append(_call_from_record(record, summary))
            continue
        for row in sorted(linked, key=lambda item: _aware(item["created_at"])):
            calls.append(_call_from_llm(row, summary))
    seen_primary = False
    for call in calls:
        if call.get("role"):
            continue
        call["role"] = "primary" if not seen_primary else "other"
        seen_primary = True
    return calls


def _group_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    order: list[tuple[str, str]] = []
    for record in records:
        key = _bucket_key(record)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(record)
    turns: list[dict[str, Any]] = []
    for key in order:
        rows = buckets[key]
        kinds = {_kind(row) for row in rows}
        turns.append(
            {
                "bucket": key,
                "records": rows,
                "summary_only": all(_is_summary(row) for row in rows),
                "kind": "image" if kinds == {"image"} else "chat",
                "conversation_id": _conversation_id(rows),
                "created_at": _turn_time(rows),
                "link": None,
            }
        )
    return turns


def _attach_summaries(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = [turn for turn in turns if turn["summary_only"]]
    hosts = [turn for turn in turns if not turn["summary_only"] and turn["kind"] == "chat"]
    consumed: set[int] = set()
    for summary in summaries:
        conversation = summary.get("conversation_id")
        candidates = [
            host
            for host in hosts
            if conversation and host.get("conversation_id") == conversation and host["created_at"] <= summary["created_at"]
        ]
        if not candidates:
            summary["link"] = "unlinked"
            continue
        host = max(candidates, key=lambda item: item["created_at"])
        if summary["created_at"] - host["created_at"] > SUMMARY_WINDOW:
            summary["link"] = "unlinked"
            continue
        host["records"].extend(summary["records"])
        host["link"] = "conversation_latest_turn"
        consumed.add(id(summary))
    return [turn for turn in turns if id(turn) not in consumed]


def _assign_prompts(turns: list[dict[str, Any]], messages: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for turn in turns:
        turn["conversation_title"] = _title(turn["records"])
        if turn["kind"] != "chat" or not turn.get("conversation_id"):
            turn["prompt"] = None
            turn["prompt_quality"] = "unavailable"
            continue
        grouped[turn["conversation_id"]].append(turn)
    by_conversation: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for message in messages:
        key = _conv_key(message.get("conversation_id"))
        if key:
            by_conversation[key].append(message)
    floor = datetime.min.replace(tzinfo=timezone.utc)
    for conversation, group in grouped.items():
        group.sort(key=lambda item: item["created_at"])
        rows = sorted(by_conversation.get(conversation, []), key=lambda item: _aware(item["created_at"]))
        previous = floor
        for turn in group:
            end = turn["created_at"] + timedelta(seconds=5)
            window = [row for row in rows if previous < _aware(row["created_at"]) <= end and str(row.get("content") or "").strip()]
            if window:
                turn["prompt"] = _snippet(window[-1]["content"])
                turn["prompt_quality"] = "actual"
                previous = _aware(window[-1]["created_at"])
            else:
                turn["prompt"] = None
                turn["prompt_quality"] = "unavailable"
                previous = turn["created_at"]


def _quality_from_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return _figure(None, "unavailable")
    methods = [str(item.get("attribution_method") or "unavailable") for item in items]
    values = [_int_or_none(item.get("token_count")) for item in items]
    known = [value for value in values if value is not None]
    if not known:
        return _figure(None, "unavailable")
    if methods and all(method == "provider_reported" for method in methods):
        return _figure(sum(known), "actual")
    return _figure(sum(known), "estimated")


def _optional_num(figure: dict[str, Any] | None) -> int | None:
    if not figure or figure.get("quality") == "unavailable" or figure.get("value") is None:
        return None
    return int(figure["value"])


def _share(figure: dict[str, Any], total: int | None) -> float | None:
    if total is None or total <= 0 or figure.get("quality") == "unavailable" or figure.get("value") is None:
        return None
    return round(int(figure["value"]) / total, 4)


def _catalog(
    calls: list[dict[str, Any]],
    categories: tuple[tuple[str, str], ...],
    direction: str,
    provider_total: int | None,
    *,
    fallback: tuple[str, str] | None = None,
    skip: set[str] | None = None,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    present: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for call in calls:
        for item in call.get("breakdown") or []:
            if item.get("direction") != direction:
                continue
            present[str(item.get("category") or "")].append(item)
    any_rows = any(present.values())
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for category, label in categories:
        seen.add(category)
        if skip and category in skip and overrides and category in overrides:
            figure = overrides[category]
        elif overrides and category in overrides:
            figure = overrides[category]
        elif not any_rows and fallback and category == fallback[0]:
            figure = _figure(provider_total, "actual") if provider_total is not None else _figure(None, "unavailable")
        elif not any_rows:
            figure = _figure(None, "unavailable")
        else:
            figure = _quality_from_items(present.get(category, []))
        rows.append(
            {
                "category": category,
                "label": label,
                "direction": direction,
                "figure": figure,
                "share": _share(figure, provider_total),
            }
        )
    if any_rows:
        for category, items in present.items():
            if category in seen or (skip and category in skip):
                continue
            figure = _quality_from_items(items)
            rows.append(
                {
                    "category": category,
                    "label": EXTRA_LABELS.get(category, category),
                    "direction": direction,
                    "figure": figure,
                    "share": _share(figure, provider_total),
                }
            )
    return rows


def _exclusive_composition(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fresh = 0
    output_sum = 0
    cache_sum = 0
    known_cache = False
    unknown_cache = False
    used = False
    for row in rows:
        incoming = row.get("input")
        outgoing = row.get("output")
        cache = row.get("cache")
        if incoming is None and outgoing is None and cache is None:
            continue
        used = True
        input_value = 0 if incoming is None else int(incoming)
        output_sum += 0 if outgoing is None else int(outgoing)
        if incoming is not None and cache is not None and 0 <= int(cache) <= input_value:
            known_cache = True
            cache_sum += int(cache)
            fresh += input_value - int(cache)
        else:
            unknown_cache = True
            fresh += input_value
    if not used:
        empty = _figure(None, "unavailable")
        return {
            "cache_split": False,
            "fresh_input": empty,
            "cache_read": empty,
            "output": empty,
            "total": empty,
            "note": None,
        }
    if not known_cache:
        return {
            "cache_split": False,
            "fresh_input": _figure(fresh, "actual"),
            "cache_read": _figure(None, "unavailable"),
            "output": _figure(output_sum, "actual"),
            "total": _figure(fresh + output_sum, "actual"),
            "note": "这些记录缺少可拆分的缓存读取。环形图按服务商输入总量展示，没有把缺失缓存记成 0。",
        }
    note = "缓存读取是输入的一部分。环形图使用互斥分片：普通输入 = 已拆分输入 − 缓存读取。"
    if unknown_cache:
        note += " 没有缓存字段的调用整段计入普通输入。"
    return {
        "cache_split": True,
        "fresh_input": _figure(fresh, "actual"),
        "cache_read": _figure(cache_sum, "actual"),
        "output": _figure(output_sum, "actual"),
        "total": _figure(fresh + cache_sum + output_sum, "actual"),
        "note": note,
    }


def _composition_rows(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for turn in turns:
        for record in turn["records"]:
            linked = record.get("llm") or []
            if linked:
                for call in linked:
                    rows.append(
                        {
                            "input": call.get("input_tokens"),
                            "output": call.get("output_tokens"),
                            "cache": call.get("cache_read_tokens"),
                        }
                    )
                continue
            context = record.get("context") or {}
            rows.append(
                {
                    "input": _int_or_none(context.get("prompt_tokens")) if "prompt_tokens" in context else None,
                    "output": _int_or_none(context.get("completion_tokens")) if "completion_tokens" in context else None,
                    "cache": None,
                }
            )
    return rows


def _finish_turn(turn: dict[str, Any], tools: list[dict[str, Any]], skills: list[dict[str, Any]], transactions: list[dict[str, Any]]) -> None:
    records = turn["records"]
    calls = _calls_for(records)
    turn["calls"] = calls
    turn["created_at"] = _turn_time(records)
    turn["conversation_id"] = _conversation_id(records)
    turn["model"] = next((call.get("model") for call in calls if call.get("role") == "primary" and call.get("model")), None)
    if turn["model"] is None:
        turn["model"] = next((call.get("model") for call in calls if call.get("model")), None)
    turn["mode"] = next(
        (
            call.get("runtime_mode")
            for call in calls
            if call.get("role") != "summary" and call.get("runtime_mode") in {"ask", "plan", "agent"}
        ),
        None,
    )
    turn["billed"] = sum(_int(row.get("amount")) for row in records)
    turn["billed_tokens"] = _figure(turn["billed"], "actual")
    turn["input_tokens"] = _ledger_figure(records, "prompt_tokens")
    turn["output_tokens"] = _ledger_figure(records, "completion_tokens")
    turn["cache_read_tokens"] = _sum_figures([call["cache_read_tokens"] for call in calls]) if calls else _figure(None, "unavailable")
    turn["cache_write_tokens"] = _sum_figures([call["cache_write_tokens"] for call in calls]) if calls else _figure(None, "unavailable")
    turn["call_count"] = len(calls)
    turn["summary_count"] = sum(1 for call in calls if call.get("role") == "summary")
    image_count = 0
    seen_image = False
    for row in records:
        value = _int_or_none((row.get("context") or {}).get("image_count"))
        if value:
            image_count += value
            seen_image = True
    turn["image_count"] = image_count if seen_image else None
    results = {row.get("result") or "settled" for row in records}
    if "exceeded" in results:
        turn["result"] = "exceeded"
    elif results == {"unsettled"}:
        turn["result"] = "unsettled"
    elif "unsettled" in results:
        turn["result"] = "partial"
    else:
        turn["result"] = "settled"
    keys = {row.get("settlement_key") for row in records if row.get("settlement_key")}
    if turn["bucket"][0] == "run":
        keys.add(turn["bucket"][1])
    turn["tools"] = [tool for tool in tools if tool.get("task_id") and tool["task_id"] in keys]
    provider_ids = {call["id"] for call in calls if call.get("origin") == "provider"}
    turn["skills"] = [skill for skill in skills if skill.get("llm_usage_id") and str(skill["llm_usage_id"]) in provider_ids]
    turn["tool_count"] = len(turn["tools"])
    turn["transactions"] = [
        item
        for item in transactions
        if item.get("settlement_key") and item["settlement_key"] in {row.get("settlement_key") for row in records}
    ]
    provider_input = _optional_num(turn["input_tokens"])
    provider_output = _optional_num(turn["output_tokens"])
    reasoning_override = None
    reported = [call["reasoning_tokens"] for call in calls if call["reasoning_tokens"]["quality"] == "actual"]
    if calls and len(reported) == len(calls):
        reasoning_override = _sum_figures(reported)
    turn["input_breakdown"] = _catalog(
        calls,
        INPUT_CATEGORIES,
        "input",
        provider_input,
        fallback=("other_input", "未归因输入"),
    )
    turn["output_breakdown"] = _catalog(
        calls,
        OUTPUT_CATEGORIES,
        "output",
        provider_output,
        fallback=("other_output", "其他输出"),
        skip={"reasoning"} if reasoning_override else None,
        overrides={"reasoning": reasoning_override} if reasoning_override else None,
    )
    turn["cache"] = [
        {
            "category": "cache_read",
            "label": "缓存读取",
            "direction": "cache",
            "figure": turn["cache_read_tokens"],
            "share": None,
        },
        {
            "category": "cache_write",
            "label": "缓存写入",
            "direction": "cache",
            "figure": turn["cache_write_tokens"],
            "share": None,
        },
        {
            "category": "cache_miss",
            "label": "缓存未命中",
            "direction": "cache",
            "figure": _figure(None, "unavailable"),
            "share": None,
        },
    ]
    groups = []
    for role in ("primary", "summary", "other"):
        role_calls = [call for call in calls if call.get("role") == role]
        if not role_calls:
            continue
        groups.append(
            {
                "role": role,
                "label": ROLE_LABELS[role],
                "count": len(role_calls),
                "input_tokens": _sum_figures([call["input_tokens"] for call in role_calls]),
                "output_tokens": _sum_figures([call["output_tokens"] for call in role_calls]),
                "total_tokens": _sum_figures([call["total_tokens"] for call in role_calls]),
            }
        )
    turn["call_groups"] = groups
    turn["timeline"] = _timeline(turn)
    turn["technical"] = _technical(turn)


def _timeline(turn: dict[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    prompt_time = turn["created_at"]
    if turn.get("prompt_quality") == "actual":
        nodes.append(
            {
                "id": f"user:{turn['records'][0]['id']}",
                "kind": "user_submit",
                "title": "用户提交",
                "name": turn.get("prompt"),
                "started_at": prompt_time,
                "duration_ms": None,
                "tokens": None,
                "status": "completed",
                "event_id_masked": None,
                "detail": None,
                "_order": 0,
            }
        )
    else:
        nodes.append(
            {
                "id": f"user:{turn['records'][0]['id']}",
                "kind": "user_submit",
                "title": "用户提交",
                "name": None,
                "started_at": None,
                "duration_ms": None,
                "tokens": None,
                "status": "unrecorded",
                "event_id_masked": None,
                "detail": "没有找到对应该轮的用户消息。",
                "_order": 0,
            }
        )
    nodes.append(
        {
            "id": f"context:{turn['records'][0]['id']}",
            "kind": "context",
            "title": "上下文准备",
            "name": None,
            "started_at": None,
            "duration_ms": None,
            "tokens": None,
            "status": "unrecorded",
            "event_id_masked": None,
            "detail": "上下文准备没有单独计量，相关 Token 记在模型输入里。",
            "_order": 1,
        }
    )
    for call in turn["calls"]:
        kind = "summary" if call.get("role") == "summary" else "model"
        title = "摘要调用" if kind == "summary" else "主模型调用" if call.get("role") == "primary" else "模型调用"
        nodes.append(
            {
                "id": f"{kind}:{call['id']}",
                "kind": kind,
                "title": title,
                "name": call.get("model"),
                "started_at": call.get("started_at") or call.get("created_at"),
                "duration_ms": _duration_ms(call.get("started_at"), call.get("completed_at")),
                "tokens": call.get("total_tokens"),
                "status": call.get("status") or "completed",
                "event_id_masked": mask_identifier(call["id"]),
                "detail": "这次调用的 Token 来自对应的模型用量事件。",
                "_order": 2,
            }
        )
    for tool in sorted(turn["tools"], key=lambda item: _aware(item["created_at"])):
        nodes.append(
            {
                "id": f"tool:{tool['id']}",
                "kind": "tool",
                "title": "工具调用",
                "name": tool.get("tool_name"),
                "started_at": tool.get("created_at"),
                "duration_ms": tool.get("duration_ms"),
                "tokens": None,
                "status": tool.get("status") or "completed",
                "event_id_masked": mask_identifier(str(tool["id"])),
                "detail": "工具执行本身不另计 LLM Token。",
                "_order": 2,
            }
        )
    for skill in sorted(turn["skills"], key=lambda item: _aware(item["created_at"])):
        tokens = skill.get("instruction_tokens")
        nodes.append(
            {
                "id": f"skill:{skill['id']}",
                "kind": "skill",
                "title": "Skill 执行",
                "name": skill.get("skill_name"),
                "started_at": skill.get("created_at"),
                "duration_ms": skill.get("duration_ms"),
                "tokens": _figure(tokens, "estimated" if tokens is not None else "unavailable"),
                "status": skill.get("status") or "completed",
                "event_id_masked": mask_identifier(skill.get("skill_execution_id")),
                "detail": "Skill 指令 Token 已包含在模型输入中，这里不重复计入本轮总额。",
                "_order": 2,
            }
        )
    nodes.append(
        {
            "id": f"answer:{turn['records'][0]['id']}",
            "kind": "answer",
            "title": "最终回答",
            "name": None,
            "started_at": None,
            "duration_ms": None,
            "tokens": None,
            "status": "unrecorded",
            "event_id_masked": None,
            "detail": "最终回答没有单独的用量事件，Token 记在模型输出里。",
            "_order": 3,
        }
    )
    nodes.sort(key=lambda item: (item["_order"], _aware(item["started_at"]) if item.get("started_at") else datetime.max.replace(tzinfo=timezone.utc)))
    for item in nodes:
        item.pop("_order", None)
    return nodes


def _technical(turn: dict[str, Any]) -> dict[str, Any]:
    methods: list[str] = []
    raw_usage = []
    request_ids: list[str] = []
    for call in turn["calls"]:
        for item in call.get("breakdown") or []:
            method = str(item.get("attribution_method") or "")
            if method and method not in methods:
                methods.append(method)
        masked_request = mask_identifier(call.get("request_id"))
        if masked_request and masked_request not in request_ids:
            request_ids.append(masked_request)
        raw_usage.append(
            {
                "call_id_masked": mask_identifier(call["id"]),
                "model": call.get("model"),
                "provider": call.get("provider"),
                "usage_source": call.get("usage_source"),
                "input_tokens": call["input_tokens"]["value"],
                "output_tokens": call["output_tokens"]["value"],
                "total_tokens": call["total_tokens"]["value"],
                "reasoning_tokens": call["reasoning_tokens"]["value"],
                "cache_read_tokens": call["cache_read_tokens"]["value"],
                "cache_write_tokens": call["cache_write_tokens"]["value"],
                "input_quality": call["input_tokens"]["quality"],
                "output_quality": call["output_tokens"]["quality"],
                "total_quality": call["total_tokens"]["quality"],
            }
        )
    seen_tx: set[str] = set()
    billing = []
    for item in turn["transactions"]:
        key = str(item.get("settlement_key") or "")
        seen_tx.add(key)
        billing.append(
            {
                "action": item.get("action") or "consume",
                "amount": _int(item.get("amount")),
                "settlement_key_masked": mask_identifier(key),
                "quality": "actual",
                "source": "quota_transaction",
            }
        )
    for record in turn["records"]:
        key = str(record.get("settlement_key") or "")
        if key in seen_tx:
            continue
        billing.append(
            {
                "action": "consume",
                "amount": _int(record.get("amount")),
                "settlement_key_masked": mask_identifier(key) if key else None,
                "quality": "actual",
                "source": "usage_record",
            }
        )
    execution_ids: list[str] = []
    for tool in turn["tools"]:
        masked = mask_identifier(tool.get("task_id"))
        if masked and masked not in execution_ids:
            execution_ids.append(masked)
    for skill in turn["skills"]:
        masked = mask_identifier(skill.get("skill_execution_id"))
        if masked and masked not in execution_ids:
            execution_ids.append(masked)
    provider = next((call.get("provider") for call in turn["calls"] if call.get("provider")), None)
    return {
        "turn_id_masked": mask_identifier(str(turn["records"][0]["id"])) or "****",
        "execution_ids_masked": execution_ids,
        "request_ids_masked": request_ids,
        "provider": provider,
        "model": turn.get("model"),
        "raw_usage": raw_usage,
        "billing_events": billing,
        "usage_source": "provider" if any(call.get("origin") == "provider" for call in turn["calls"]) else "quota_ledger",
        "attribution_methods": methods,
        "pricing_version": "未提供",
        "summary_link": turn.get("link"),
    }


def _public_turn(turn: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(sorted(turn["records"], key=lambda row: _aware(row["created_at"]))[0]["id"]),
        "kind": turn["kind"],
        "mode": turn.get("mode"),
        "model": turn.get("model"),
        "created_at": turn["created_at"],
        "prompt": turn.get("prompt"),
        "prompt_quality": turn.get("prompt_quality") or "unavailable",
        "conversation_title": turn.get("conversation_title"),
        "billed_tokens": turn["billed_tokens"],
        "input_tokens": turn["input_tokens"],
        "output_tokens": turn["output_tokens"],
        "cache_read_tokens": turn["cache_read_tokens"],
        "cache_write_tokens": turn["cache_write_tokens"],
        "call_count": turn["call_count"],
        "summary_count": turn["summary_count"],
        "tool_count": turn["tool_count"],
        "image_count": turn.get("image_count"),
        "result": turn["result"],
        "link": turn.get("link"),
        "call_groups": turn["call_groups"],
        "input_breakdown": turn["input_breakdown"],
        "output_breakdown": turn["output_breakdown"],
        "cache": turn["cache"],
        "timeline": turn["timeline"],
        "technical": turn["technical"],
    }


def _day_points(turns: list[dict[str, Any]], since: datetime | None, until: datetime | None, range_key: str) -> list[dict[str, Any]]:
    buckets: dict[Any, dict[str, int]] = defaultdict(lambda: {"chat": 0, "image": 0, "input_tokens": 0, "output_tokens": 0})
    for turn in turns:
        local = _aware(turn["created_at"]).astimezone(SHANGHAI)
        slot = buckets[local.date()]
        slot["chat" if turn["kind"] == "chat" else "image"] += int(turn["billed"])
        slot["input_tokens"] += _num(turn["input_tokens"])
        slot["output_tokens"] += _num(turn["output_tokens"])
    if range_key == "all" or since is None or until is None:
        dates = sorted(buckets)
        if len(dates) > 60:
            dates = dates[-60:]
        return [_day_point(day, buckets[day], full=True) for day in dates]
    start = _aware(since).astimezone(SHANGHAI).date()
    end = _aware(until).astimezone(SHANGHAI).date()
    points = []
    cursor = start
    while cursor <= end:
        points.append(_day_point(cursor, buckets[cursor], full=False))
        cursor += timedelta(days=1)
    return points


def _day_point(day: Any, slot: dict[str, int], *, full: bool) -> dict[str, Any]:
    label = day.strftime("%Y-%m-%d" if full else "%m-%d")
    return {
        "label": label,
        "chat": slot["chat"],
        "image": slot["image"],
        "input_tokens": slot["input_tokens"],
        "output_tokens": slot["output_tokens"],
    }


def _round_points(turns: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    ordered = sorted(turns, key=lambda item: item["created_at"])
    limited = len(ordered) > 40
    tail = ordered[-40:]
    points = []
    for turn in tail:
        local = _aware(turn["created_at"]).astimezone(SHANGHAI)
        billed = int(turn["billed"])
        points.append(
            {
                "label": local.strftime("%m-%d %H:%M"),
                "chat": billed if turn["kind"] == "chat" else 0,
                "image": billed if turn["kind"] == "image" else 0,
                "input_tokens": _num(turn["input_tokens"]),
                "output_tokens": _num(turn["output_tokens"]),
            }
        )
    return points, limited


def _gaps(turns: list[dict[str, Any]], composition: dict[str, Any], truncated: bool, message_limited: bool) -> list[str]:
    gaps: list[str] = []
    if turns and not composition.get("cache_split"):
        gaps.append("有些记录缺少可拆分的缓存读取，环形图没有把缺失缓存画成 0。")
    if any(not record.get("llm") and _kind(record) == "chat" for turn in turns for record in turn["records"]):
        gaps.append("部分对话没有模型调用明细，对应的输入分类显示为未提供。")
    if truncated:
        gaps.append("只分析了最近 1000 条额度记录。")
    if message_limited:
        gaps.append("用户消息过多，部分问题摘要没有取出。")
    return gaps


def _actual(value: int | None) -> dict[str, Any]:
    if value is None:
        return _figure(None, "unavailable")
    return _figure(int(value), "actual")


def _part_label(direction: str, category: str) -> str:
    if direction == "output" and category == "image":
        return "图片"
    return CATEGORY_LABELS.get(category, category)


def _public_parts(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        count = row.get("token_count")
        method = str(row.get("attribution_method") or "unavailable")
        if count is None or int(count) <= 0 or method == "unavailable":
            continue
        direction = str(row.get("direction") or "input")
        category = str(row.get("category") or "")
        quality = "actual" if method == "provider_reported" else "estimated"
        items.append(
            {
                "category": category,
                "label": _part_label(direction, category),
                "direction": direction if direction in {"input", "output", "cache"} else "input",
                "figure": _figure(int(count), quality),
                "share": None,
            }
        )
    return items


def _public_summary(row: dict[str, Any]) -> dict[str, Any]:
    text = str(row.get("summary_text") or "").strip()
    model = str(row.get("model") or "").strip()
    return {
        "id": str(row.get("id") or ""),
        "model": model or None,
        "called_at": row.get("called_at"),
        "summary": text or None,
        "call_count": int(row.get("call_count") or 1),
        "tool_count": int(row.get("tool_count") or 0),
        "input_tokens": _actual(row.get("input_tokens")),
        "output_tokens": _actual(row.get("output_tokens")),
        "total_tokens": _actual(row.get("total_tokens")),
        "input_breakdown": _public_parts(row.get("input_breakdown")),
        "output_breakdown": _public_parts(row.get("output_breakdown")),
        "cache": _public_parts(row.get("cache_breakdown")),
    }


def _provider_label(provider: str | None) -> str | None:
    text = str(provider or "").strip()
    if not text:
        return None
    return PROVIDER_LABELS.get(text, text)


def _turn_provider(turn: dict[str, Any]) -> str | None:
    for record in turn.get("records") or []:
        for row in record.get("llm") or []:
            if row.get("provider"):
                return str(row["provider"])
        if record.get("source") == "image":
            return "ark"
    return None


def _track_kind(track: str, category: str, api: str) -> str | None:
    if track == "image" or category in {"vision", "image_gen"} or api == "images":
        return "视觉"
    if category == "video_gen" or api == "videos":
        return "视频"
    if track == "text" or category or api:
        return "文本"
    return None


def _turn_tracks(turns: list[dict[str, Any]]) -> set[str]:
    present = set()
    for turn in turns:
        if turn.get("summary_only"):
            continue
        present.add("image" if turn.get("kind") == "image" else "text")
    if present:
        return present
    return {"image" if turn.get("kind") == "image" else "text" for turn in turns}


def _tracks_in_view(rows: list[dict[str, Any]], turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """筛选后的轮次里没有的轨道不出现在卡片上。全部视图两种都在时两行都保留。"""
    present = _turn_tracks(turns)
    if not present or present >= {"text", "image"}:
        return rows
    if present == {"image"}:
        return [row for row in rows if row.get("track") == "image"]
    return [row for row in rows if row.get("track") != "image"]


def _public_tracks(session: dict[str, Any] | None, turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stored = (session or {}).get("model_tracks")
    rows: list[dict[str, Any]] = []
    if isinstance(stored, list):
        for item in stored:
            if not isinstance(item, dict):
                continue
            turn_count = int(item.get("turns") or 0)
            if turn_count <= 0:
                continue
            rows.append(
                {
                    "track": str(item.get("track") or "text"),
                    "model": str(item.get("modelName") or item.get("model_name") or "") or None,
                    "model_kind": _track_kind(
                        str(item.get("track") or ""),
                        str(item.get("modelCategory") or item.get("model_category") or ""),
                        str(item.get("modelApi") or item.get("model_api") or ""),
                    ),
                    "provider": _provider_label(item.get("providerId") or item.get("provider_id")),
                    "turn_count": turn_count,
                    "billed_tokens": int(item.get("tokens") or 0),
                }
            )
    rows = _tracks_in_view(rows, turns)
    if rows:
        return rows
    grouped: dict[str, dict[str, Any]] = {}
    for turn in turns:
        track = "image" if turn.get("kind") == "image" else "text"
        bucket = grouped.setdefault(
            track,
            {"track": track, "model": None, "provider": None, "turn_count": 0, "billed_tokens": 0},
        )
        if not turn.get("summary_only"):
            bucket["turn_count"] += 1
        bucket["billed_tokens"] += int(turn.get("billed") or 0)
        if turn.get("model"):
            bucket["model"] = str(turn["model"])
        provider = _turn_provider(turn)
        if provider:
            bucket["provider"] = _provider_label(provider)
    derived = [item for item in grouped.values() if item["turn_count"] > 0]
    if len(derived) < 2:
        return rows
    for item in derived:
        item["model_kind"] = "视觉" if item["track"] == "image" else "文本"
    return derived


def _model_kind(session: dict[str, Any] | None, turns: list[dict[str, Any]]) -> str | None:
    category = str((session or {}).get("model_category") or "")
    api = str((session or {}).get("model_api") or "")
    if category in {"vision", "image_gen"} or api == "images":
        return "视觉"
    if category == "video_gen" or api == "videos":
        return "视频"
    if category in {"chat", "text"} or api == "chat":
        return "文本"
    if any(turn.get("kind") == "image" for turn in turns):
        return "视觉"
    if (session or {}).get("model_name") or any(turn.get("model") for turn in turns):
        return "文本"
    return None


def _link_orphan_images(records: list[dict[str, Any]], sessions: list[dict[str, Any]]) -> None:
    candidates = [
        item
        for item in sessions
        if item.get("has_images") and (item.get("conversation_id") or item.get("history_key")) and item.get("updated_at")
    ]
    if not candidates:
        return
    for record in records:
        if record.get("source") != "image":
            continue
        context = dict(record.get("context") or {})
        if _conv_key(context.get("conversation_id")):
            continue
        created = _aware(record["created_at"])
        best = None
        best_delta = None
        for item in candidates:
            delta = abs((created - _aware(item["updated_at"])).total_seconds())
            if delta > 7200:
                continue
            if best_delta is None or delta < best_delta:
                best = item
                best_delta = delta
        if best is None:
            continue
        context["conversation_id"] = best.get("conversation_id") or best.get("history_key")
        if not context.get("conversation_title") and best.get("title"):
            context["conversation_title"] = best["title"]
        record["context"] = context
    anchors = []
    for record in records:
        if record.get("source") != "image":
            continue
        context = record.get("context") or {}
        key = _conv_key(context.get("conversation_id"))
        if key:
            anchors.append((_aware(record["created_at"]), key, context.get("conversation_title")))
    if not anchors:
        return
    for record in records:
        if not _is_summary(record):
            continue
        context = dict(record.get("context") or {})
        if _conv_key(context.get("conversation_id")):
            continue
        created = _aware(record["created_at"])
        best = None
        best_delta = None
        for moment, key, title in anchors:
            delta = abs((created - moment).total_seconds())
            if delta > 1800:
                continue
            if best_delta is None or delta < best_delta:
                best = (key, title)
                best_delta = delta
        if best is None:
            continue
        context["conversation_id"] = best[0]
        if best[1] and not context.get("conversation_title"):
            context["conversation_title"] = best[1]
        record["context"] = context


def _link_summary_rows(summaries: list[dict[str, Any]] | None, records: list[dict[str, Any]]) -> None:
    anchors = []
    for record in records:
        if record.get("source") != "image":
            continue
        context = record.get("context") or {}
        key = _conv_key(context.get("conversation_id"))
        if key:
            anchors.append((_aware(record["created_at"]), key))
    if not anchors:
        return
    for row in summaries or []:
        if _conv_key(row.get("conversation_id")):
            continue
        called = row.get("called_at")
        if called is None:
            continue
        moment = _aware(called)
        best = None
        best_delta = None
        for anchor, key in anchors:
            delta = abs((moment - anchor).total_seconds())
            if delta > 1800:
                continue
            if best_delta is None or delta < best_delta:
                best = key
                best_delta = delta
        if best:
            row["conversation_id"] = best


def _conversation_card(
    key: str,
    group: list[dict[str, Any]],
    session: dict[str, Any] | None,
    summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    ordered = sorted(group, key=lambda item: item["created_at"])
    title = str((session or {}).get("title") or "").strip()
    if not title:
        title = next((str(item.get("conversation_title")) for item in ordered if item.get("conversation_title")), "")
    if not title:
        title = next((str(item.get("prompt")) for item in ordered if item.get("prompt")), "")
    model = str((session or {}).get("model_name") or "").strip()
    if not model:
        model = next((str(item.get("model")) for item in reversed(ordered) if item.get("model")), "")
    provider = _provider_label((session or {}).get("provider_id")) or _provider_label(_turn_provider(ordered[-1]))
    turn_count = int((session or {}).get("turn_count") or 0)
    if turn_count <= 0:
        turn_count = sum(1 for item in ordered if not item.get("summary_only"))
    summary_rows = sorted(summaries, key=lambda item: _aware(item.get("called_at")))
    if _turn_tracks(ordered) == {"image"}:
        summary_rows = []
    return {
        "id": key,
        "title": title or "未命名对话",
        "created_at": ordered[0]["created_at"],
        "model": model or None,
        "model_kind": _model_kind(session, ordered),
        "provider": provider,
        "turn_count": turn_count,
        "billed_tokens": sum(int(item["billed"]) for item in ordered),
        "tracks": _public_tracks(session, ordered),
        "summaries": [_public_summary(item) for item in summary_rows],
        "turns": [_public_turn(item) for item in ordered],
    }


def _public_conversations(
    turns: list[dict[str, Any]],
    summaries: list[dict[str, Any]] | None,
    sessions: list[dict[str, Any]] | None,
    offset: int,
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    parent: dict[str, str] = {}

    def find(key: str) -> str:
        parent.setdefault(key, key)
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(left: str, right: str) -> None:
        parent[find(left)] = find(right)

    session_for = {}
    for item in sessions or []:
        aliases = [alias for alias in (item.get("conversation_id"), item.get("history_key")) if alias]
        if not aliases:
            continue
        for alias in aliases[1:]:
            union(aliases[0], alias)
        session_for[id(item)] = (aliases, item)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    solos: list[dict[str, Any]] = []
    for turn in turns:
        key = turn.get("conversation_id")
        if key:
            grouped[find(key)].append(turn)
        else:
            solos.append(turn)
    summary_by_root: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in summaries or []:
        key = _conv_key(item.get("conversation_id"))
        if key:
            summary_by_root[find(key)].append(item)

    def matched_session(root: str) -> dict[str, Any] | None:
        for aliases, item in session_for.values():
            if any(find(alias) == root for alias in aliases):
                return item
        return None

    cards = [
        _conversation_card(key, group, matched_session(key), summary_by_root.get(key, []))
        for key, group in grouped.items()
    ]
    for turn in solos:
        cards.append(_conversation_card(f"solo:{_public_turn(turn)['id']}", [turn], None, []))
    cards.sort(key=lambda item: item["created_at"], reverse=True)
    return cards[offset : offset + limit], len(cards)


def build_usage_analysis(
    records: list[dict[str, Any]],
    llm_records: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    skills: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    *,
    kind: str | None = None,
    model: str | None = None,
    mode: str | None = None,
    query: str | None = None,
    limit: int = 20,
    offset: int = 0,
    truncated: bool = False,
    range_key: str = "30d",
    since: datetime | None = None,
    until: datetime | None = None,
    message_limited: bool = False,
    summaries: list[dict[str, Any]] | None = None,
    sessions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    _link_orphan_images(records, sessions or [])
    _link_summary_rows(summaries, records)
    assign_llm(records, llm_records)
    turns = _attach_summaries(_group_records(records))
    _assign_prompts(turns, messages)
    for turn in turns:
        _finish_turn(turn, tools, skills, transactions)
    kind_turns = [turn for turn in turns if not kind or turn["kind"] == kind]
    models = sorted({turn["model"] for turn in kind_turns if turn.get("model")})
    filtered = kind_turns
    if model:
        filtered = [turn for turn in filtered if turn.get("model") == model]
    if mode:
        filtered = [turn for turn in filtered if turn.get("mode") == mode]
    needle = (query or "").strip().casefold()
    if needle:
        filtered = [
            turn
            for turn in filtered
            if needle in " ".join([turn.get("prompt") or "", turn.get("conversation_title") or ""]).casefold()
        ]
    billed_total = sum(int(turn["billed"]) for turn in filtered)
    summary = {
        "total": billed_total,
        "chat": sum(int(turn["billed"]) for turn in filtered if turn["kind"] == "chat"),
        "image": sum(int(turn["billed"]) for turn in filtered if turn["kind"] == "image"),
        "count": len(filtered),
    }
    composition = _exclusive_composition(_composition_rows(filtered))
    composition["billed_total"] = billed_total
    provider_total = composition["total"]["value"]
    if provider_total is not None and provider_total != billed_total:
        extra = f"额度扣减 {billed_total}，服务商输入加输出 {provider_total}。"
        composition["note"] = f"{composition['note']} {extra}".strip() if composition.get("note") else extra
    trend_round, trend_round_limited = _round_points(filtered)
    filtered.sort(key=lambda item: item["created_at"], reverse=True)
    page = filtered[offset : offset + limit]
    conversations, conversation_total = _public_conversations(filtered, summaries, sessions, offset, limit)
    return {
        "summary": summary,
        "composition": composition,
        "trend_day": _day_points(filtered, since, until, range_key),
        "trend_round": trend_round,
        "trend_round_limited": trend_round_limited,
        "models": models,
        "items": [_public_turn(turn) for turn in page],
        "conversations": conversations,
        "conversation_total": conversation_total,
        "total": len(filtered),
        "limit": limit,
        "offset": offset,
        "truncated": truncated,
        "gaps": _gaps(filtered, composition, truncated, message_limited),
        "range_key": range_key,
    }


def _map_record(row: UsageRecord) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "metric": row.metric,
        "amount": int(row.amount or 0),
        "source": row.source,
        "settlement_key": row.settlement_key,
        "result": row.result,
        "created_at": row.created_at,
        "context": dict(row.context_json or {}),
    }


def _map_llm(row: LlmUsageRecord, breakdown: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "settlement_key": row.settlement_key,
        "conversation_id": row.conversation_id,
        "task_id": row.task_id,
        "request_id": row.request_id,
        "provider": row.provider,
        "model": row.model,
        "runtime_mode": row.runtime_mode,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "reasoning_tokens": row.reasoning_tokens,
        "cache_read_tokens": row.cache_read_tokens,
        "cache_write_tokens": row.cache_write_tokens,
        "total_tokens": row.total_tokens,
        "usage_source": row.usage_source,
        "status": row.status,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "created_at": row.created_at,
        "breakdown": breakdown,
    }


async def load_usage_analysis(
    session: AsyncSession,
    user_id: uuid.UUID | str,
    *,
    range_key: str,
    kind: str | None,
    model: str | None,
    mode: str | None,
    query: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    owner = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    now = datetime.now(timezone.utc)
    days = {"7d": 7, "30d": 30, "90d": 90}.get(range_key)
    since = now - timedelta(days=days) if days else None
    count_stmt = select(func.count(UsageRecord.id)).where(UsageRecord.user_id == owner)
    list_stmt = select(UsageRecord).where(UsageRecord.user_id == owner)
    if since is not None:
        count_stmt = count_stmt.where(UsageRecord.created_at >= since)
        list_stmt = list_stmt.where(UsageRecord.created_at >= since)
    total = int(await session.scalar(count_stmt) or 0)
    rows = list(await session.scalars(list_stmt.order_by(UsageRecord.created_at.desc()).limit(RECORD_LIMIT)))
    records = [_map_record(row) for row in rows]
    floor = since - timedelta(minutes=5) if since is not None else None
    llm_stmt = select(LlmUsageRecord).where(LlmUsageRecord.user_id == owner)
    tool_stmt = select(ToolUsageRecord).where(ToolUsageRecord.user_id == owner)
    tx_stmt = select(QuotaTransaction).where(QuotaTransaction.user_id == owner, QuotaTransaction.action == "consume")
    if floor is not None:
        llm_stmt = llm_stmt.where(LlmUsageRecord.created_at >= floor)
        tool_stmt = tool_stmt.where(ToolUsageRecord.created_at >= floor)
        tx_stmt = tx_stmt.where(QuotaTransaction.created_at >= floor)
    llm_rows = list(await session.scalars(llm_stmt.order_by(LlmUsageRecord.created_at.desc()).limit(3000)))
    breakdowns: dict[str, list[dict[str, Any]]] = defaultdict(list)
    llm_ids = [row.id for row in llm_rows]
    if llm_ids:
        detail_rows = await session.scalars(select(LlmUsageBreakdown).where(LlmUsageBreakdown.usage_record_id.in_(llm_ids)))
        for item in detail_rows:
            breakdowns[str(item.usage_record_id)].append(
                {
                    "direction": item.direction,
                    "category": item.category,
                    "token_count": item.token_count,
                    "attribution_method": item.attribution_method,
                }
            )
    skill_rows: list[SkillUsageRecord] = []
    if llm_ids:
        skill_rows = list(
            await session.scalars(
                select(SkillUsageRecord).where(
                    SkillUsageRecord.user_id == owner,
                    SkillUsageRecord.llm_usage_id.in_(llm_ids),
                )
            )
        )
    tool_rows = list(await session.scalars(tool_stmt.order_by(ToolUsageRecord.created_at.desc()).limit(2000)))
    tx_rows = list(await session.scalars(tx_stmt.order_by(QuotaTransaction.created_at.desc()).limit(2000)))
    conversation_ids: list[uuid.UUID] = []
    for record in records:
        key = _conv_key((record.get("context") or {}).get("conversation_id"))
        if not key:
            continue
        try:
            parsed = uuid.UUID(key)
        except ValueError:
            continue
        if parsed not in conversation_ids:
            conversation_ids.append(parsed)
    messages: list[dict[str, Any]] = []
    message_limited = False
    if conversation_ids:
        message_stmt = (
            select(ConversationMessage.session_id, ConversationMessage.content, ConversationMessage.created_at)
            .join(ConversationSession, ConversationMessage.session_id == ConversationSession.id)
            .where(
                ConversationSession.user_id == owner,
                ConversationSession.id.in_(conversation_ids),
                ConversationMessage.role == "user",
            )
            .order_by(ConversationMessage.created_at.asc())
            .limit(MESSAGE_LIMIT)
        )
        fetched = (await session.execute(message_stmt)).all()
        message_limited = len(fetched) >= MESSAGE_LIMIT
        messages = [
            {"conversation_id": str(session_id), "content": content, "created_at": created_at}
            for session_id, content, created_at in fetched
        ]
    history = await _load_history_sessions(session, owner)
    summary_rows = await _load_summary_updates(session, owner, floor)
    return build_usage_analysis(
        records,
        [_map_llm(row, breakdowns.get(str(row.id), [])) for row in llm_rows],
        [
            {
                "id": str(row.id),
                "task_id": row.task_id,
                "tool_name": row.tool_name,
                "status": row.status,
                "duration_ms": row.duration_ms,
                "created_at": row.created_at,
                "llm_usage_id": str(row.llm_usage_id) if row.llm_usage_id else None,
            }
            for row in tool_rows
        ],
        [
            {
                "id": str(row.id),
                "llm_usage_id": str(row.llm_usage_id) if row.llm_usage_id else None,
                "skill_name": row.skill_name,
                "skill_execution_id": row.skill_execution_id,
                "status": row.status,
                "duration_ms": row.duration_ms,
                "instruction_tokens": row.instruction_tokens,
                "created_at": row.created_at,
            }
            for row in skill_rows
        ],
        [
            {
                "settlement_key": row.settlement_key,
                "action": row.action,
                "amount": int(row.amount or 0),
                "created_at": row.created_at,
            }
            for row in tx_rows
        ],
        messages,
        kind=kind,
        model=model,
        mode=mode,
        query=query,
        limit=limit,
        offset=offset,
        truncated=total > RECORD_LIMIT,
        range_key=range_key,
        since=since,
        until=now,
        message_limited=message_limited,
        summaries=summary_rows,
        sessions=history,
    )


async def _load_history_sessions(session: AsyncSession, owner: uuid.UUID) -> list[dict[str, Any]]:
    rows = list(await session.scalars(select(ChatHistorySession).where(ChatHistorySession.user_id == owner)))
    if not rows:
        return []
    ids = [row.id for row in rows]
    counts = await session.execute(
        select(ChatHistoryMessage.session_id, func.count())
        .where(ChatHistoryMessage.session_id.in_(ids), ChatHistoryMessage.role == "user")
        .group_by(ChatHistoryMessage.session_id)
    )
    count_map = {session_id: int(count) for session_id, count in counts.all()}
    image_ids = set(
        await session.scalars(
            select(ChatHistoryMessage.session_id).where(
                ChatHistoryMessage.session_id.in_(ids),
                func.jsonb_typeof(ChatHistoryMessage.generated_images) == "array",
                func.jsonb_array_length(ChatHistoryMessage.generated_images) > 0,
            )
        )
    )
    loaded: list[dict[str, Any]] = []
    for row in rows:
        key = _conv_key(row.server_conversation_id)
        history_key = f"chat:{row.chat_id}" if row.chat_id else None
        if not key and not history_key:
            continue
        loaded.append(
            {
                "conversation_id": key,
                "history_key": history_key,
                "title": row.chat_title or "",
                "model_name": row.model_name,
                "model_category": row.model_category,
                "model_api": row.model_api,
                "provider_id": row.provider_id,
                "model_tracks": row.model_tracks if isinstance(row.model_tracks, list) else [],
                "turn_count": count_map.get(row.id, 0),
                "has_images": row.id in image_ids,
                "updated_at": row.updated_at,
            }
        )
    return loaded


async def _load_summary_updates(
    session: AsyncSession,
    owner: uuid.UUID,
    floor: datetime | None,
) -> list[dict[str, Any]]:
    stmt = select(SummaryUpdate).where(SummaryUpdate.user_id == owner)
    if floor is not None:
        stmt = stmt.where(SummaryUpdate.called_at >= floor)
    rows = list(await session.scalars(stmt.order_by(SummaryUpdate.called_at.asc())))
    return [
        {
            "id": str(row.id),
            "conversation_id": row.conversation_id,
            "model": row.model,
            "summary_text": row.summary_text,
            "called_at": row.called_at,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "total_tokens": row.total_tokens,
            "input_breakdown": list(row.input_breakdown or []),
            "output_breakdown": list(row.output_breakdown or []),
            "cache_breakdown": list(row.cache_breakdown or []),
            "tool_count": int(row.tool_count or 0),
            "call_count": int(row.call_count or 1),
        }
        for row in rows
    ]
