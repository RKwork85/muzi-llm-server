"""无副作用的本地文本工具。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

_MAX_DEPTH = 32


class FormatJsonArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=8000)


class TextStatsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=8000)


class DateDiffArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str = Field(min_length=8, max_length=40)
    end: str = Field(min_length=8, max_length=40)

    @field_validator("start", "end")
    @classmethod
    def _utc_text(cls, value: str) -> str:
        _parse_utc(value)
        return value


async def format_json(args: FormatJsonArgs) -> str:
    _reject_deep(args.text)
    try:
        payload = json.loads(args.text)
    except json.JSONDecodeError as exc:
        raise ValueError("json") from exc
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
    if len(rendered) > 8000:
        raise ValueError("json")
    return json.dumps(
        {"formatted": rendered, "truncated": False, "untrusted": False, "source": "local"},
        ensure_ascii=False,
    )


async def text_stats(args: TextStatsArgs) -> str:
    return json.dumps(
        {
            "characters": len(args.text),
            "lines": args.text.count("\n") + 1,
            "words": len(args.text.split()),
            "truncated": False,
            "untrusted": False,
            "source": "local",
        },
        ensure_ascii=False,
    )


async def date_diff(args: DateDiffArgs) -> str:
    start = _parse_utc(args.start)
    end = _parse_utc(args.end)
    delta = end - start
    total = int(delta.total_seconds())
    absolute = abs(total)
    return json.dumps(
        {
            "total_seconds": total,
            "days": absolute // 86400,
            "hours": (absolute % 86400) // 3600,
            "minutes": (absolute % 3600) // 60,
            "seconds": absolute % 60,
            "negative": total < 0,
            "timezone": "UTC",
            "truncated": False,
            "untrusted": False,
            "source": "local",
        },
        ensure_ascii=False,
    )


def _parse_utc(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("date") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _reject_deep(text: str) -> None:
    depth = 0
    in_string = False
    escape = False
    for char in text:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
            if depth > _MAX_DEPTH:
                raise ValueError("json")
        elif char in "}]":
            depth = max(0, depth - 1)
