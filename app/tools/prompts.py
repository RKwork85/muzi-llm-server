"""按工具名加载提示词。文案在 app/prompts/tools.toml。

toml 是数据，不是代码：改一句文案不应该重启进程，更不应该靠 uvicorn 的
``--reload-include`` 去监视 ``*.toml``——那样改一个标点就会重启整个应用
（重连数据库、清空内存态），而且在生产环境（无 reload）根本不会生效。

这里的口径：用「文件指纹（mtime_ns + size）」判断内容是否变化。没变时零解析
开销，变了下次读取立刻生效。改动只作用于当前进程，多 worker 各自发现，无需重启。
"""

from __future__ import annotations

import threading
import tomllib
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "prompts" / "tools.toml"

_lock = threading.Lock()
_cache: dict[str, str] = {}
_stamp: tuple[int, int] | None = None


def _load(path: Path) -> dict[str, str]:
    with path.open("rb") as handle:
        loaded = tomllib.load(handle)
    prompts: dict[str, str] = {}
    for name, text in loaded.items():
        if not isinstance(name, str) or not isinstance(text, str) or not text.strip():
            raise ValueError(f"工具提示词不合法: {name}")
        prompts[name] = text.strip()
    return prompts


def _prompts() -> dict[str, str]:
    """按文件指纹缓存：mtime 或大小变化即重载，返回只读用途的字典。"""
    global _stamp
    with _lock:
        try:
            stat = _PATH.stat()
        except OSError:
            raise ValueError(f"工具提示词文件缺失: {_PATH}") from None
        stamp = (stat.st_mtime_ns, stat.st_size)
        if _stamp == stamp and _cache:
            return _cache
        prompts = _load(_PATH)
        _cache.clear()
        _cache.update(prompts)
        _stamp = stamp
        return _cache


def tool_prompt(name: str) -> str:
    text = _prompts().get(name)
    if not text:
        raise ValueError(f"缺少工具提示词: {name}")
    return text
