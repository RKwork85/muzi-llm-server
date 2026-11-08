"""按需读取技能包内的引用。不执行文件，也不抓取网址。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from app.skills.errors import SkillError, SkillValidationError
from app.skills.paths import normalize_resource_path

_ALLOWED_EXTENSIONS = {".md", ".txt", ".json"}
_MAX_RESOURCE = 32768
_CONTEXT_KINDS = {"reference", "asset", "template"}


def read_resource(package: Path, relative_path: str) -> tuple[str, str, int]:
    try:
        relative, kind = normalize_resource_path(relative_path)
    except SkillValidationError as exc:
        raise SkillError("SKILL_RESOURCE_NOT_ALLOWED", "资源路径无效") from exc
    if kind not in _CONTEXT_KINDS:
        raise SkillError("SKILL_RESOURCE_NOT_ALLOWED", "该资源不能载入上下文")
    suffix = Path(relative).suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise SkillError("SKILL_RESOURCE_NOT_ALLOWED", "资源类型无效")
    root = package.resolve()
    candidate = Path(root, *relative.split("/"))
    if candidate.is_symlink() or any(parent.is_symlink() for parent in candidate.parents if parent != candidate.anchor):
        resolved = candidate.resolve()
        if os.path.commonpath([str(root), str(resolved)]) != str(root):
            raise SkillError("SKILL_RESOURCE_NOT_ALLOWED", "资源路径无效")
    target = candidate.resolve()
    if os.path.commonpath([str(root), str(target)]) != str(root) or not target.is_file():
        raise SkillError("SKILL_RESOURCE_NOT_ALLOWED", "资源路径无效")
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise SkillError("SKILL_PACKAGE_INVALID", "技能资源无效") from exc
    if len(raw) > _MAX_RESOURCE:
        raise SkillError("SKILL_CONTENT_TOO_LARGE", "技能资源过大")
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise SkillError("SKILL_PACKAGE_INVALID", "技能资源无效") from exc
    digest = hashlib.sha256(raw).hexdigest()
    return text, digest, len(raw)
