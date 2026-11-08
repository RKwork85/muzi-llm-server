"""解析 SKILL.md 顶部清单。不执行正文，也不访问磁盘。"""

from __future__ import annotations

import hashlib

from app.skills.errors import SkillValidationError
from app.skills.models import SkillManifest

_KEYS = {"name", "description", "license", "compatibility", "metadata", "version"}
_DEFAULT_VERSION = "1.0.0"


def parse_skill_markdown(text: str) -> tuple[SkillManifest, str]:
    raw = text.encode("utf-8")
    if len(raw) > 65536:
        raise SkillValidationError("SKILL_TOO_LARGE", "技能说明过长")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if normalized.startswith("\ufeff"):
        normalized = normalized[1:]
    if not normalized.startswith("---\n"):
        raise SkillValidationError("SKILL_FRONTMATTER_INVALID", "技能说明缺少清单")
    end = normalized.find("\n---\n", 4)
    if end < 0:
        raise SkillValidationError("SKILL_FRONTMATTER_INVALID", "技能说明缺少清单")
    fields = _fields(normalized[4:end])
    body = normalized[end + 5 :]
    if not body.strip():
        raise SkillValidationError("SKILL_BODY_EMPTY", "技能说明为空")
    version = fields.get("version") or _DEFAULT_VERSION
    metadata = fields.get("metadata") or {}
    if "version" in metadata and metadata["version"] != version:
        raise SkillValidationError("SKILL_VERSION_MISMATCH", "版本号不一致")
    encoded = body.encode("utf-8")
    try:
        manifest = SkillManifest(
            name=fields.get("name", ""),
            description=fields.get("description", ""),
            version=version,
            license=fields.get("license"),
            compatibility=fields.get("compatibility"),
            metadata=metadata,
            instruction_sha256=hashlib.sha256(encoded).hexdigest(),
            body_bytes=len(encoded),
        )
    except ValueError as exc:
        raise SkillValidationError("SKILL_MANIFEST_INVALID", "技能清单无效") from exc
    return manifest, body


def _fields(header: str) -> dict[str, object]:
    found: dict[str, object] = {}
    metadata: dict[str, str] | None = None
    for line in header.split("\n"):
        if not line.strip():
            continue
        if line.startswith("  ") and metadata is not None:
            key, value = _pair(line.strip())
            metadata[key] = value
            continue
        if line[:1].isspace():
            raise SkillValidationError("SKILL_FRONTMATTER_INVALID", "技能清单格式无效")
        key, value = _pair(line)
        if key not in _KEYS:
            raise SkillValidationError("SKILL_FRONTMATTER_INVALID", "技能清单含未知字段")
        if key == "metadata":
            if value:
                raise SkillValidationError("SKILL_FRONTMATTER_INVALID", "技能清单格式无效")
            metadata = {}
            found["metadata"] = metadata
            continue
        if key in found:
            raise SkillValidationError("SKILL_FRONTMATTER_INVALID", "技能清单字段重复")
        found[key] = value
        metadata = None
    return found


def _pair(line: str) -> tuple[str, str]:
    if ":" not in line:
        raise SkillValidationError("SKILL_FRONTMATTER_INVALID", "技能清单格式无效")
    key, value = line.split(":", 1)
    key = key.strip()
    value = value.strip()
    if not key or len(key) > 32:
        raise SkillValidationError("SKILL_FRONTMATTER_INVALID", "技能清单格式无效")
    if len(value) > 1024:
        raise SkillValidationError("SKILL_FRONTMATTER_INVALID", "技能清单字段过长")
    return key, value
