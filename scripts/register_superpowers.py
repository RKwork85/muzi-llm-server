"""把 vendor/superpowers/skills 登记为已启用的系统技能。

重复执行时，同名技能保持原记录，不覆盖。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.skills.frontmatter import parse_skill_markdown
from app.skills.models import SkillAction, SkillActor, SkillQuery, SkillScope, SkillStatus
from app.skills.registry import FileSkillRegistry

SOURCE = ROOT / "vendor" / "superpowers" / "skills"
TARGET = ROOT / "skills_data"


def main() -> None:
    if not SOURCE.is_dir():
        raise SystemExit(f"找不到技能目录：{SOURCE}")
    registry = FileSkillRegistry(TARGET, allow_system=True)
    actor = SkillActor()
    existing = {
        item.name: item
        for item in registry.query(SkillQuery(actor=actor, scope=SkillScope.SYSTEM))
        if item.status != SkillStatus.UNINSTALLED
    }
    lines: list[str] = []
    for path in sorted(SOURCE.rglob("SKILL.md")):
        manifest, _body = parse_skill_markdown(path.read_text(encoding="utf-8"))
        current = existing.get(manifest.name)
        if current is None:
            imported = registry.scan_directory(
                path.parent,
                actor=actor,
                label="superpowers",
                scope=SkillScope.SYSTEM,
            )
            skill_id = imported[0].metadata.skill_id
        else:
            skill_id = current.skill_id
        skill = registry.get(skill_id, actor=actor)
        if skill.metadata.status == SkillStatus.REGISTERED:
            registry.apply(skill_id, SkillAction.INSTALL, actor=actor)
            skill = registry.get(skill_id, actor=actor)
        if skill.metadata.status == SkillStatus.INSTALLED:
            registry.apply(skill_id, SkillAction.ENABLE, actor=actor)
        lines.append(f"{manifest.name} {skill_id}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
