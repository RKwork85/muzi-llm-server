"""按名称加载服务端提示词，并校验模板变量。"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.exceptions import PromptNotFoundError, PromptVariableError

_NAME = re.compile(r"^[a-z0-9_]+$")
_VARIABLE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
# .txt 优先；.md 用于较长的模式说明（如 plan_mode.md）
_SUFFIXES = (".txt", ".md")


class PromptService:
    def __init__(self, root: Path | None = None) -> None:
        default_root = Path(__file__).resolve().parents[1] / "prompts" / "system"
        self._root = (root or default_root).resolve()

    def names(self) -> list[str]:
        if not self._root.is_dir():
            return []
        stems = {
            path.stem
            for suffix in _SUFFIXES
            for path in self._root.glob(f"*{suffix}")
            if _NAME.fullmatch(path.stem)
        }
        return sorted(stems)

    def render(self, name: str, variables: dict[str, str] | None = None) -> str:
        variables = variables or {}
        template = self._read(name)
        missing = sorted({item for item in _VARIABLE.findall(template) if item not in variables})
        if missing:
            raise PromptVariableError(missing)

        def replace(match: re.Match[str]) -> str:
            return variables[match.group(1)]

        return _VARIABLE.sub(replace, template)

    def _read(self, name: str) -> str:
        if not _NAME.fullmatch(name):
            raise PromptNotFoundError()
        for suffix in _SUFFIXES:
            path = (self._root / f"{name}{suffix}").resolve()
            if path.parent != self._root or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            if text.strip():
                return text
        raise PromptNotFoundError()
