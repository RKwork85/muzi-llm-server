"""按工具名加载提示词。文案在 app/prompts/tools.toml。"""

from __future__ import annotations

import tomllib
from functools import cache
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "prompts" / "tools.toml"


@cache
def _prompts() -> dict[str, str]:
    with _PATH.open("rb") as handle:
        loaded = tomllib.load(handle)
    prompts: dict[str, str] = {}
    for name, text in loaded.items():
        if not isinstance(name, str) or not isinstance(text, str) or not text.strip():
            raise ValueError(f"工具提示词不合法: {name}")
        prompts[name] = text.strip()
    return prompts


def tool_prompt(name: str) -> str:
    text = _prompts().get(name)
    if not text:
        raise ValueError(f"缺少工具提示词: {name}")
    return text
