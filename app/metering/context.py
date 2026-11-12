"""非流式调用在当前任务里暂存分类，结算时一次取走。"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_PENDING: ContextVar[list[dict[str, Any]] | None] = ContextVar("muzi_usage_meters", default=None)


def begin_meters() -> None:
    _PENDING.set([])


def add_meter(meter: dict[str, Any]) -> None:
    bucket = _PENDING.get()
    if bucket is None:
        bucket = []
        _PENDING.set(bucket)
    bucket.append(meter)


def take_meters() -> list[dict[str, Any]]:
    bucket = _PENDING.get() or []
    _PENDING.set([])
    return list(bucket)
