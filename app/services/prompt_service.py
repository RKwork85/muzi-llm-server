"""按名称加载服务端提示词，并校验模板变量。"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.exceptions import PromptNotFoundError, PromptVariableError
from app.prompts.settings import is_enabled, prompt_id_for

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
        # 被管理员关掉的提示词当成「不存在」：调用方要么回退到兜底人设
        # （render_first），要么显式吞掉错误（规划引擎），两种行为都已有约定。
        if not is_enabled(prompt_id_for(name)):
            raise PromptNotFoundError()
        template = self._read(name)
        missing = sorted({item for item in _VARIABLE.findall(template) if item not in variables})
        if missing:
            raise PromptVariableError(missing)

        def replace(match: re.Match[str]) -> str:
            return variables[match.group(1)]

        return _VARIABLE.sub(replace, template)

    def render_first(
        self, names: list[str], variables: dict[str, str] | None = None
    ) -> str:
        """按顺序尝试候选提示词，跳过被关闭或缺失的；全部不可用时返回空串。

        模板变量缺失仍要抛错：那说明调用方用错了提示词，静默回退成空人设
        只会让模型毫无约束地回答。
        """
        for name in names:
            try:
                return self.render(name, variables)
            except PromptNotFoundError:
                continue
        return ""

    def raw(self, name: str) -> str:
        """读取原始模板（保留 ``{{变量}}``），供提示词管理面板展示。"""
        return self._read(name)

    def exists(self, name: str) -> bool:
        """提示词文件是否存在（不考虑开关状态）。"""
        if not _NAME.fullmatch(name):
            return False
        for suffix in _SUFFIXES:
            path = (self._root / f"{name}{suffix}").resolve()
            if path.parent != self._root or not path.is_file():
                continue
            try:
                if path.read_text(encoding="utf-8").strip():
                    return True
            except OSError:
                continue
        return False

    def source(self, name: str) -> str:
        """提示词文件来源，展示给人看用。"""
        for suffix in _SUFFIXES:
            path = (self._root / f"{name}{suffix}").resolve()
            if path.parent == self._root and path.is_file():
                return f"app/prompts/system/{name}{suffix}"
        return ""

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
