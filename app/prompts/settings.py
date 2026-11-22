"""内置提示词的全局启用状态。

提示词文案仍然留在源码 / 数据文件里，这份设置只记录「哪些被关掉」。
状态落盘为一个 JSON 文件（``data_store/prompt_settings.json``），按文件指纹热加载：
改完下一次组装提示词立刻生效，不需要重启进程。

为什么不做成用户偏好：这些是服务端人设、插件说明和工具描述，属于系统级配置，
被某个普通用户关掉会直接改变所有人的对话行为。接口层只允许管理员写入。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from app.core.config import ROOT

logger = logging.getLogger("muzi_llm")

# 与 data_store 的其它运行时数据放在一起（该目录已在 .gitignore 里）。
_DEFAULT_PATH = ROOT / "data_store" / "prompt_settings.json"
_PATH = _DEFAULT_PATH

# 不是人设的人设文件：plan_mode.md 只给规划引擎用，归类到「模式说明」。
_MODE_PROMPTS = frozenset({"plan_mode"})

_lock = threading.RLock()
_cache: frozenset[str] = frozenset()
_stamp: tuple[int, int] | None = None


def prompt_id_for(name: str) -> str:
    """提示词文件名 -> 目录里的稳定 id。"""
    return f"mode:{name}" if name in _MODE_PROMPTS else f"persona:{name}"


def _load_disabled(path: Path) -> frozenset[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("prompt settings unreadable path=%s", path, exc_info=True)
        return frozenset()
    values = payload.get("disabled") if isinstance(payload, dict) else payload
    if not isinstance(values, list):
        return frozenset()
    return frozenset(str(item).strip() for item in values if isinstance(item, str) and item.strip())


def disabled_prompts() -> frozenset[str]:
    """当前被关闭的提示词 id 集合。文件不存在或内容损坏时按「全部开启」处理。"""
    global _cache, _stamp
    with _lock:
        try:
            stat = _PATH.stat()
        except OSError:
            _stamp = None
            _cache = frozenset()
            return _cache
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp != _stamp:
            _cache = _load_disabled(_PATH)
            _stamp = stamp
        return _cache


def is_enabled(prompt_id: str) -> bool:
    return prompt_id not in disabled_prompts()


def _write(disabled: frozenset[str]) -> None:
    global _cache, _stamp
    path = _PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    # 先写临时文件再原子替换：并发请求读到半截 JSON 会被当成「全部开启」，
    # 那一轮就会把管理员刚关掉的提示词重新注入。
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp.write_text(
        json.dumps({"disabled": sorted(disabled)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, path)
    _cache = disabled
    try:
        stat = path.stat()
    except OSError:
        _stamp = None
    else:
        _stamp = (stat.st_mtime_ns, stat.st_size)


def set_enabled(prompt_id: str, enabled: bool) -> frozenset[str]:
    """打开 / 关闭单条提示词，返回更新后的关闭集合。"""
    with _lock:
        current = set(disabled_prompts())
        if enabled:
            current.discard(prompt_id)
        else:
            current.add(prompt_id)
        result = frozenset(current)
        _write(result)
        return result


def replace_disabled(prompt_ids: list[str]) -> frozenset[str]:
    """整体替换关闭集合（用于「全部开启 / 全部关闭」这类批量操作）。"""
    with _lock:
        result = frozenset(str(item).strip() for item in prompt_ids if str(item).strip())
        _write(result)
        return result


def reset_for_tests(path: Path | None = None) -> None:
    """测试辅助：切到临时文件并清空缓存。"""
    global _PATH, _cache, _stamp
    with _lock:
        _PATH = path or _DEFAULT_PATH
        _cache = frozenset()
        _stamp = None
