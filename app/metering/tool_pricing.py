"""工具固定计费：系统内部只认 token 额度。

工具本身不直接扣额度，工具结果以 tool_result 形式计入下一轮模型输入。
但某些工具（如文件交付、图片生成）有独立成本，需要按「成功调用」扣固定 token。

计费口径：
- free：0。
- fixed_tokens：每次成功调用扣固定 token（失败返回 0）。
  按张计费（unit="image"）时，扣 = 每张 token × 成功张数。

人民币 → token 的换算在充值/定价系统完成，这里不做任何货币运算。
固定 token 默认值在 ToolPricing 定义处给出，运行期可用环境变量
TOOL_PRICE_<NAME>_TOKENS 覆盖（NAME 转大写、非字母数字转下划线）。
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Any


def _env_key(tool_name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9]+", "_", (tool_name or "").upper()).strip("_")
    if not name:
        return ""
    return f"TOOL_PRICE_{name}_TOKENS"


@lru_cache(maxsize=1)
def _load_env_file() -> None:
    """把 .env 里的 TOOL_PRICE_* 加载进 os.environ（进程环境变量优先）。"""
    try:
        from app.core.config import ROOT
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except Exception:
        pass


def resolve_tokens(tool_name: str, default: int) -> int:
    """返回工具的固定 token 值：环境变量 TOOL_PRICE_<NAME>_TOKENS 优先，否则用默认值。"""
    _load_env_file()
    key = _env_key(tool_name)
    if key:
        raw = os.environ.get(key)
        if raw is not None and raw.strip():
            try:
                return max(0, int(raw))
            except ValueError:
                pass
    return max(0, int(default or 0))


def quote_tool_tokens(
    pricing: Any | None,
    *,
    tool_name: str = "",
    success: bool = True,
    units: int = 1,
) -> int:
    """把一次工具调用换算成 token 额度。

    - free：0。
    - fixed_tokens：成功时按「每单位 token × 单位数」扣（unit=call 时单位数恒为 1，
      unit=image 时单位数 = 图片张数），失败返回 0。
    """
    if pricing is None:
        return 0
    model = getattr(pricing, "model", "free")
    if model != "fixed_tokens":
        return 0
    if not success:
        return 0
    tokens = resolve_tokens(tool_name, getattr(pricing, "tokens", 0) or 0)
    unit = getattr(pricing, "unit", "call") or "call"
    multiplier = max(0, int(units or 0)) if unit == "image" else 1
    return tokens * multiplier
