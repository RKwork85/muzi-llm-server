"""把一次模型调用拆成互斥的输入/输出分类。

Provider 返回的 prompt/completion/total 是计费权威值。本地按字符估算的分类
只标记为 heuristic_estimated，差额记入 other_input / other_output，不能倒过来
当成供应商账单。
"""

from __future__ import annotations

import json
from typing import Any

from app.providers.base import UsageStats

INPUT_CATEGORIES = (
    "system",
    "developer",
    "user",
    "history",
    "tool_schema",
    "tool_result",
    "skill_instruction",
    "retrieval",
    "file",
    "image",
    "audio",
    "video",
    "other_input",
)
OUTPUT_CATEGORIES = (
    "reasoning",
    "answer",
    "tool_call",
    "structured_output",
    "other_output",
)


def estimate_tokens(text: str) -> int:
    """粗估，不是供应商 tokenizer。汉字计 1，其余非空白约 4 个字符计 1。"""
    if not text:
        return 0
    cjk = 0
    other = 0
    for char in text:
        if "\u4e00" <= char <= "\u9fff":
            cjk += 1
        elif not char.isspace():
            other += 1
    return cjk + (other + 3) // 4


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts)


def _media_segments(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    rows: list[dict[str, Any]] = []
    file_text: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "image_url":
            rows.append(_unavailable("input", "image"))
        elif kind in {"input_audio", "audio_url"}:
            rows.append(_unavailable("input", "audio"))
        elif kind in {"video_url", "input_video"}:
            rows.append(_unavailable("input", "video"))
        elif kind == "file":
            text = block.get("text") if isinstance(block.get("text"), str) else ""
            if text:
                file_text.append(text)
            else:
                rows.append(_unavailable("input", "file"))
    if file_text:
        rows.append(_estimated("input", "file", "\n".join(file_text)))
    return rows


def _row(
    direction: str,
    category: str,
    token_count: int | None,
    method: str,
    confidence: str | None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "direction": direction,
        "category": category,
        "token_count": token_count,
        "attribution_method": method,
        "confidence": confidence,
        "metadata": metadata or {},
    }


def _estimated(direction: str, category: str, text: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return _row(direction, category, estimate_tokens(text), "heuristic_estimated", "0.4", metadata)


def _unavailable(direction: str, category: str) -> dict[str, Any]:
    return _row(direction, category, None, "unavailable", None, {})


def _provider(direction: str, category: str, token_count: int, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return _row(direction, category, token_count, "provider_reported", "1", metadata)


def _call_id_to_name(messages: list[dict[str, Any]]) -> dict[str, str]:
    """从 assistant 消息的 tool_calls 构建 tool_call_id → tool_name 映射。"""
    mapping: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for tc in message.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            call_id = tc.get("id")
            fn = tc.get("function")
            name = fn.get("name") if isinstance(fn, dict) else None
            if call_id and name:
                mapping[str(call_id)] = str(name)
    return mapping


def tool_usage_rows(
    messages: list[dict[str, Any]],
    tool_calls: list[Any],
) -> list[dict[str, Any]]:
    """按工具名聚合一轮对话的工具调用次数，token 一律记 0。

    工具行的 token 不再用字符估算：上下文字符 token 已在 breakdown 的
    tool_call / tool_result 分类里体现，这里不重复估算。免费工具记 0，
    固定/外部定价由 resource 事件单独下发 external_tokens，前端据此展示「固定计费」。
    """
    by_name: dict[str, dict[str, int]] = {}

    def bump(name: str, calls: int = 0) -> None:
        row = by_name.setdefault(name, {"calls": 0, "tokens": 0})
        row["calls"] += calls

    for call in tool_calls:
        name = getattr(call, "name", None)
        if not name:
            continue
        bump(str(name), 1)

    call_id_to_name = _call_id_to_name(messages)
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        call_id = str(message.get("tool_call_id") or "")
        name = call_id_to_name.get(call_id) or "tool"
        bump(name)

    return [
        {"tool_name": name, "calls": row["calls"], "tokens": row["tokens"]}
        for name, row in by_name.items()
    ]


def message_segments(messages: list[dict[str, Any]], *, skip_system: bool) -> list[tuple[str, str, list[dict[str, Any]]]]:
    """每条消息只进入一个文本分类。工具结果优先于历史，避免同一段文字计两次。"""
    last_user = -1
    for index, message in enumerate(messages):
        if message.get("role") == "user":
            last_user = index
    segments: list[tuple[str, str, list[dict[str, Any]]]] = []
    for index, message in enumerate(messages):
        role = message.get("role")
        content = message.get("content")
        media = _media_segments(content)
        text = _text_of(content)
        if role == "system":
            if skip_system:
                if media:
                    segments.append(("", "", media))
                continue
            segments.append(("system", text, media))
        elif role == "developer":
            segments.append(("developer", text, media))
        elif role == "tool":
            segments.append(("tool_result", text, media))
        elif role == "user":
            category = "user" if index == last_user else "history"
            segments.append((category, text, media))
        elif role == "assistant":
            extra = ""
            tool_calls = message.get("tool_calls")
            if tool_calls:
                extra = json.dumps(tool_calls, ensure_ascii=False)
            segments.append(("history", "\n".join(part for part in (text, extra) if part), media))
    return segments


def build_meter(
    *,
    provider_name: str,
    model: str,
    runtime_mode: str,
    usage_source: str,
    status: str,
    usage: UsageStats | None,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    system_text: str,
    developer_text: str,
    skill_text: str,
    skill_refs: list[dict[str, Any]] | None,
    output_text: str,
    tool_calls: list[Any],
    structured: bool,
) -> dict[str, Any]:
    skip_system = bool(system_text or developer_text or skill_text)
    breakdown: list[dict[str, Any]] = []
    if system_text:
        breakdown.append(_estimated("input", "system", system_text))
    if developer_text:
        breakdown.append(_estimated("input", "developer", developer_text))
    if skill_text:
        breakdown.append(_estimated("input", "skill_instruction", skill_text))
    if tools:
        breakdown.append(_estimated("input", "tool_schema", json.dumps(tools, ensure_ascii=False)))
    for category, text, media in message_segments(messages, skip_system=skip_system):
        breakdown.extend(media)
        if category and text:
            breakdown.append(_estimated("input", category, text))

    estimated_input = sum(
        int(row["token_count"])
        for row in breakdown
        if row["direction"] == "input"
        and row["attribution_method"] == "heuristic_estimated"
        and row["token_count"] is not None
    )
    prompt = usage.prompt_tokens if usage is not None else None
    completion = usage.completion_tokens if usage is not None else None
    total = usage.total_tokens if usage is not None else None
    reasoning = usage.reasoning_tokens if usage is not None else None
    cache_read = usage.cache_read_tokens if usage is not None else None
    cache_write = usage.cache_write_tokens if usage is not None else None

    if prompt is not None:
        gap = prompt - estimated_input
        if gap > 0:
            breakdown.append(
                _row(
                    "input",
                    "other_input",
                    gap,
                    "heuristic_estimated",
                    "0.4",
                    {"reason": "provider_remainder"},
                )
            )
        elif gap < 0:
            breakdown.append(
                _row(
                    "input",
                    "other_input",
                    None,
                    "unavailable",
                    None,
                    {"reason": "estimate_exceeds_provider", "over_by": -gap},
                )
            )

    if cache_read is not None:
        breakdown.append(_provider("cache", "cache_read", cache_read))
        if prompt is not None:
            breakdown.append(_provider("cache", "uncached_input", max(0, prompt - cache_read)))
    if cache_write is not None:
        breakdown.append(_provider("cache", "cache_write", cache_write))

    if reasoning is not None:
        breakdown.append(_provider("output", "reasoning", reasoning))
    tool_arguments = "\n".join(getattr(call, "arguments", "") or "" for call in tool_calls)
    tool_estimate = estimate_tokens(tool_arguments) if tool_arguments else 0
    if tool_estimate:
        breakdown.append(_estimated("output", "tool_call", tool_arguments))
    answer_category = "structured_output" if structured else "answer"
    answer_estimate = estimate_tokens(output_text) if output_text else 0
    if answer_estimate:
        breakdown.append(_estimated("output", answer_category, output_text))
    if completion is not None:
        known = (reasoning or 0) + tool_estimate + answer_estimate
        # reasoning 未提供时不能把它当成 0 去挤占回答；只在供应商给了 reasoning 时扣减。
        if reasoning is None:
            known = tool_estimate + answer_estimate
        gap = completion - known
        if gap > 0:
            breakdown.append(
                _row(
                    "output",
                    "other_output",
                    gap,
                    "heuristic_estimated",
                    "0.4",
                    {"reason": "provider_remainder"},
                )
            )

    skills = []
    for item in skill_refs or []:
        if not isinstance(item, dict):
            continue
        skills.append(
            {
                "skill_id": item.get("skill_id"),
                "skill_name": item.get("skill_name"),
                "skill_version": item.get("skill_version"),
                "instruction_tokens": estimate_tokens(skill_text) if skill_text else None,
                "direct_cost": None,
            }
        )

    return {
        "provider": provider_name,
        "model": model,
        "runtime_mode": runtime_mode,
        "usage_source": usage_source,
        "status": status,
        "input_tokens": prompt,
        "output_tokens": completion,
        "reasoning_tokens": reasoning,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "total_tokens": total,
        "breakdown": breakdown,
        "tool_usage": tool_usage_rows(messages, tool_calls),
        "skills": skills,
    }


def annotate_provider_usage(
    *,
    provider_name: str,
    model: str,
    messages: list[Any],
    usage_raw: dict[str, Any],
    output_text: str,
    runtime_mode: str = "ask",
    usage_source: str = "chat",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """把只有总量的 Provider usage 补上分类。分类是估算，总量仍用 Provider 数字。"""
    from app.metering.persist import usage_public
    from app.providers.parsing import parse_usage

    raw = dict(usage_raw)
    if raw.get("prompt_tokens") is None and raw.get("input_tokens") is not None:
        raw["prompt_tokens"] = raw["input_tokens"]
    if raw.get("completion_tokens") is None and raw.get("output_tokens") is not None:
        raw["completion_tokens"] = raw["output_tokens"]
    stats = parse_usage(raw)
    if stats is None:
        stats = UsageStats()
    if stats.total_tokens <= 0:
        stats.total_tokens = stats.prompt_tokens + stats.completion_tokens
    safe_messages = [item for item in messages if isinstance(item, dict)]
    meter = build_meter(
        provider_name=provider_name,
        model=model,
        runtime_mode=runtime_mode,
        usage_source=usage_source,
        status="completed",
        usage=stats,
        messages=safe_messages,
        tools=None,
        system_text="",
        developer_text="",
        skill_text="",
        skill_refs=None,
        output_text=output_text,
        tool_calls=[],
        structured=False,
    )
    public = usage_public(stats)
    public["breakdown"] = meter["breakdown"]
    return public, meter


def _image_user_content(prompt: str, images: Any) -> str | list[dict[str, Any]]:
    refs: list[str] = []
    if isinstance(images, str) and images:
        refs = [images]
    elif isinstance(images, list):
        refs = [item for item in images if isinstance(item, str) and item]
    if not refs:
        return prompt
    blocks: list[dict[str, Any]] = []
    if prompt:
        blocks.append({"type": "text", "text": prompt})
    for url in refs:
        blocks.append({"type": "image_url", "image_url": {"url": url}})
    return blocks


def annotate_image_usage(
    *,
    provider_name: str,
    model: str,
    prompt: str,
    images: Any,
    usage_raw: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """图片生成沿用文本的分类规则。

    提示词按用户输入估算，差额记入未归因输入。
    服务商给出的输出总量记为图片，不再改写成未归因输出。
    输入、输出和合计仍用服务商数字，估算不计入额度。
    """
    public, meter = annotate_provider_usage(
        provider_name=provider_name,
        model=model,
        messages=[{"role": "user", "content": _image_user_content(prompt, images)}],
        usage_raw=usage_raw,
        output_text="",
        runtime_mode="image",
        usage_source="image",
    )
    prompt_tokens = int(public.get("prompt_tokens") or 0)
    reasoning_tokens = 0
    breakdown: list[dict[str, Any]] = []
    for row in meter.get("breakdown") or []:
        # 图片模型（如 Seedream）通常只计输出、不计输入（prompt_tokens=0）。
        # 此时输入方向的启发式估算没有意义，反而会造成「输入 0 却列了用户输入 21」的矛盾，直接丢弃。
        if row.get("direction") == "input" and prompt_tokens == 0:
            continue
        if row.get("direction") == "output" and row.get("category") in {"answer", "structured_output", "other_output"}:
            continue
        if row.get("direction") == "output" and row.get("category") == "reasoning":
            reasoning_tokens = int(row.get("token_count") or 0)
        breakdown.append(row)
    completion = int(public.get("completion_tokens") or 0)
    image_tokens = max(0, completion - reasoning_tokens)
    if image_tokens:
        breakdown.append(_provider("output", "image", image_tokens))
    meter["breakdown"] = breakdown
    public["breakdown"] = breakdown
    return public, meter
