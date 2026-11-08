"""选择之后才读取正文，并重新核对身份、状态、版本和摘要。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from app.skills.discovery_models import LoadedSkill
from app.skills.errors import SkillError, SkillValidationError
from app.skills.frontmatter import parse_skill_markdown
from app.skills.models import SkillActor, SkillStatus
from app.skills.registry import FileSkillRegistry

_MAX_BODY = 65536


class SkillPackageLoader:
    def __init__(self, registry: FileSkillRegistry) -> None:
        self._registry = registry

    def load(self, skill_id: str, version: str, *, actor: SkillActor) -> LoadedSkill:
        skill = self._registry.get(skill_id, actor=actor)
        if skill.metadata.status != SkillStatus.ENABLED:
            raise SkillError("SKILL_NOT_AVAILABLE", "技能未启用")
        expected = self._registry.stored_digest(skill_id, version, actor=actor)
        package = self._registry.package_dir(skill_id, version, actor=actor)
        path = package / "SKILL.md"
        if path.is_symlink():
            raise SkillValidationError("SKILL_PATH_INVALID", "资源路径无效")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise SkillError("SKILL_PACKAGE_INVALID", "技能文件无效") from exc
        if len(raw) > _MAX_BODY:
            raise SkillError("SKILL_CONTENT_TOO_LARGE", "技能说明过长")
        try:
            text = raw.decode("utf-8")
            _manifest, body = parse_skill_markdown(text)
        except (UnicodeError, SkillValidationError) as exc:
            raise SkillError("SKILL_PACKAGE_INVALID", "技能文件无效") from exc
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if digest != expected:
            raise SkillError("SKILL_VERSION_CONFLICT", "技能内容已变化")
        return LoadedSkill(
            skill_id=skill_id,
            name=skill.metadata.name,
            version=version,
            content_digest=digest,
            instruction_body=body,
            owner_id=skill.metadata.owner_id,
            loaded_at=datetime.now(timezone.utc).isoformat(),
        )
