"""旧提示和新注册表分开。research、polish、minutes、weekly 仍只走 skill_notes。"""

from __future__ import annotations

from app.prompts.skills import SKILL_INSTRUCTIONS, skill_notes

LEGACY_SKILL_NAMES = frozenset(SKILL_INSTRUCTIONS)


def legacy_notes(names: list[str], *, execute: bool) -> str:
    return skill_notes(names, execute=execute)


def is_legacy_name(name: str) -> bool:
    return name in LEGACY_SKILL_NAMES
