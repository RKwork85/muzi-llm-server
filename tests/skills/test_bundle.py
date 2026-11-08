"""技能包（bundle）导入、列表与卸载。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from app.skills.errors import SkillError, SkillValidationError
from app.skills.models import SkillActor, SkillScope, SkillStatus
from app.skills.registry import FileSkillRegistry


def _skill_text(name: str, description: str) -> str:
    return f"""---
name: {name}
description: {description}
version: 1.0.0
metadata:
  category: dev
---
这是 {name} 的正文。
"""


def _bundle_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("superpowers/skills/brainstorm/SKILL.md", _skill_text("brainstorm", "头脑风暴"))
        archive.writestr("superpowers/skills/brainstorm/references/guide.md", b"guide")
        archive.writestr("superpowers/skills/write-plan/SKILL.md", _skill_text("write-plan", "写计划"))
        archive.writestr("superpowers/README.md", b"readme")
    return buf.getvalue()


def test_bundle_import_lists_and_uninstalls(tmp_path: Path) -> None:
    registry = FileSkillRegistry(tmp_path)
    actor = SkillActor(user_id="user-1")
    bundle = registry.import_bundle(_bundle_zip(), actor=actor, name="superpowers.zip", scope=SkillScope.USER)
    assert bundle.name == "superpowers"
    assert bundle.skill_count == 2
    assert {item.name for item in bundle.skills} == {"brainstorm", "write-plan"}
    listed = registry.list_bundles(actor=actor)
    assert len(listed) == 1
    assert listed[0].bundle_id == bundle.bundle_id
    for meta in listed[0].skills:
        assert meta.status == SkillStatus.ENABLED
        assert meta.bundle_id == bundle.bundle_id
    assert registry.read_instructions(listed[0].skills[0].skill_id, "1.0.0", actor=actor)
    registry.uninstall_bundle(bundle.bundle_id, actor=actor)
    assert registry.list_bundles(actor=actor) == []


def test_bundle_rejects_empty_or_escaping(tmp_path: Path) -> None:
    registry = FileSkillRegistry(tmp_path)
    actor = SkillActor(user_id="user-1")
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("README.md", b"nothing")
    with pytest.raises(SkillValidationError):
        registry.import_bundle(empty.getvalue(), actor=actor, name="empty.zip", scope=SkillScope.USER)
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("skills/a/SKILL.md", _skill_text("a", "a"))
        archive.writestr("../evil.txt", b"x")
    with pytest.raises(SkillValidationError):
        registry.import_bundle(bad.getvalue(), actor=actor, name="bad.zip", scope=SkillScope.USER)


def test_bundle_is_isolated_per_user(tmp_path: Path) -> None:
    registry = FileSkillRegistry(tmp_path)
    registry.import_bundle(_bundle_zip(), actor=SkillActor(user_id="user-1"), name="superpowers.zip", scope=SkillScope.USER)
    assert registry.list_bundles(actor=SkillActor(user_id="user-2")) == []
