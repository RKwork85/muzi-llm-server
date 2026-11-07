"""把浏览器持有的工作区密钥映射成目录名。密钥本身不是路径，也不写入日志。"""

from __future__ import annotations

import hashlib
import re
from contextvars import ContextVar

_KEY = re.compile(r"^[A-Za-z0-9_-]{43}$")
_SCOPE = re.compile(r"^[0-9a-f]{32}$")
DENY_SCOPE = "-"

CURRENT_WORKSPACE_SCOPE: ContextVar[str | None] = ContextVar("workspace_scope", default=None)


def scope_dirname(raw: str | None) -> str:
    """合法密钥得到 32 位十六进制目录名。缺失或非法时拒绝，不回退到共享根。"""
    if isinstance(raw, str) and _KEY.fullmatch(raw.strip()):
        return hashlib.sha256(raw.strip().encode("ascii")).hexdigest()[:32]
    return DENY_SCOPE


def is_user_scope(scope: str | None) -> bool:
    return isinstance(scope, str) and _SCOPE.fullmatch(scope) is not None
