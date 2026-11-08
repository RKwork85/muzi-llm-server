"""技能包内相对路径。拒绝越出包目录的引用。"""

from __future__ import annotations

from app.skills.errors import SkillValidationError

_ALLOWED_PREFIX = {
    "references": "reference",
    "assets": "asset",
    "scripts": "script",
    "templates": "template",
}


def normalize_resource_path(relative_path: str) -> tuple[str, str]:
    if not relative_path or len(relative_path) > 180:
        raise SkillValidationError("SKILL_PATH_INVALID", "资源路径无效")
    raw = relative_path.replace("\\", "/")
    if raw.startswith("/") or ":" in raw or "\x00" in raw:
        raise SkillValidationError("SKILL_PATH_INVALID", "资源路径无效")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise SkillValidationError("SKILL_PATH_INVALID", "资源路径无效")
    if any(part.startswith(".") or len(part) > 80 for part in parts):
        raise SkillValidationError("SKILL_PATH_INVALID", "资源路径无效")
    kind = _ALLOWED_PREFIX.get(parts[0])
    if kind is None or len(parts) < 2:
        raise SkillValidationError("SKILL_PATH_INVALID", "资源路径无效")
    return "/".join(parts), kind
