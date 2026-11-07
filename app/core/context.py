"""请求级追踪 ID。"""

from __future__ import annotations

import re
from contextvars import ContextVar, Token
from uuid import uuid4

_REQUEST_ID: ContextVar[str] = ContextVar("request_id", default="")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def normalize_request_id(raw: str | None) -> str:
    if raw and _REQUEST_ID_PATTERN.fullmatch(raw):
        return raw
    return uuid4().hex


def set_request_id(value: str) -> Token[str]:
    return _REQUEST_ID.set(value)


def reset_request_id(token: Token[str]) -> None:
    _REQUEST_ID.reset(token)


def get_request_id() -> str:
    return _REQUEST_ID.get() or "unknown"
