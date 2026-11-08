"""技能清单解析、注册、隔离和压缩包导入。"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from app.skills.errors import SkillError, SkillScopeError, SkillStateError, SkillValidationError
from app.skills.frontmatter import parse_skill_markdown
from app.skills.models import SkillAction, SkillActor, SkillQuery, SkillScope, SkillSource, SkillSourceKind, SkillStatus
from app.skills.registry import FileSkillRegistry

_TEXT = """---
name: weekly-report
description: 整理周报
license: MIT
version: 1.0.0
metadata:
  category: writing
---
按完成、进行中和阻塞来写。
"""


def _actor(user_id: str = "user-1") -> SkillActor:
    return SkillActor(user_id=user_id)


def _register(registry: FileSkillRegistry, text: str = _TEXT, user_id: str = "user-1"):
    manifest, body = parse_skill_markdown(text)
    return registry.register(
        manifest,
        body,
        actor=_actor(user_id),
        source=SkillSource(kind=SkillSourceKind.LOCAL_DIRECTORY, label="local"),
        scope=SkillScope.USER,
    )


def test_parser_rejects_a_missing_manifest() -> None:
    with pytest.raises(SkillValidationError):
        parse_skill_markdown("没有清单")
    manifest, body = parse_skill_markdown(_TEXT)
    assert manifest.name == "weekly-report"
    assert manifest.version == "1.0.0"
    assert "按完成" in body


def test_registry_keeps_body_out_of_the_index(tmp_path: Path) -> None:
    registry = FileSkillRegistry(tmp_path)
    skill = _register(registry)
    stored = json.loads(next((tmp_path / "records").glob("*.json")).read_text(encoding="utf-8"))
    assert "按完成" not in json.dumps(stored, ensure_ascii=False)
    assert registry.read_instructions(skill.metadata.skill_id, "1.0.0", actor=_actor()) == "按完成、进行中和阻塞来写。\n"
    listed = registry.query(SkillQuery(actor=_actor(), name="weekly-report"))
    assert [item.skill_id for item in listed] == [skill.metadata.skill_id]
    assert listed[0].category == "writing"


def test_same_name_conflicts_inside_one_user_and_stays_isolated(tmp_path: Path) -> None:
    registry = FileSkillRegistry(tmp_path)
    first = _register(registry)
    with pytest.raises(SkillError) as error:
        _register(registry)
    assert error.value.code == "SKILL_NAME_CONFLICT"
    second = _register(registry, user_id="user-2")
    with pytest.raises(SkillError) as hidden:
        registry.get(first.metadata.skill_id, actor=_actor("user-2"))
    assert hidden.value.code == "SKILL_NOT_FOUND"
    visible = registry.query(SkillQuery(actor=_actor("user-2")))
    assert [item.skill_id for item in visible] == [second.metadata.skill_id]


def test_system_scope_requires_an_operator_registry(tmp_path: Path) -> None:
    manifest, body = parse_skill_markdown(_TEXT)
    with pytest.raises(SkillScopeError):
        FileSkillRegistry(tmp_path).register(
            manifest,
            body,
            actor=_actor(),
            source=SkillSource(kind=SkillSourceKind.BUILTIN, label="builtin"),
            scope=SkillScope.SYSTEM,
        )
    opened = FileSkillRegistry(tmp_path / "system", allow_system=True)
    skill = opened.register(
        manifest,
        body,
        actor=SkillActor(),
        source=SkillSource(kind=SkillSourceKind.BUILTIN, label="builtin"),
        scope=SkillScope.SYSTEM,
    )
    assert skill.metadata.owner_id is None
    assert opened.get(skill.metadata.skill_id, actor=_actor("user-9")).metadata.scope == SkillScope.SYSTEM


def test_zip_import_copies_references_and_rejects_escape(tmp_path: Path) -> None:
    registry = FileSkillRegistry(tmp_path)
    good = io.BytesIO()
    with zipfile.ZipFile(good, "w") as archive:
        archive.writestr("pack/SKILL.md", _TEXT)
        archive.writestr("pack/references/guide.md", b"source")
    skill = registry.import_zip(good.getvalue(), actor=_actor(), label="upload", scope=SkillScope.USER)
    copied = tmp_path / "packages" / skill.metadata.skill_id / "1.0.0" / "references" / "guide.md"
    assert copied.read_bytes() == b"source"
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("SKILL.md", _TEXT)
        archive.writestr("../outside.txt", b"no")
    with pytest.raises(SkillValidationError):
        registry.import_zip(bad.getvalue(), actor=_actor(), label="upload", scope=SkillScope.USER)


def test_directory_scan_and_version_pin(tmp_path: Path) -> None:
    source = tmp_path / "incoming" / "weekly"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(_TEXT, encoding="utf-8")
    (source / "references").mkdir()
    (source / "references" / "guide.md").write_bytes(b"source")
    registry = FileSkillRegistry(tmp_path / "store")
    imported = registry.scan_directory(tmp_path / "incoming", actor=_actor(), label="disk", scope=SkillScope.USER)
    assert len(imported) == 1
    skill_id = imported[0].metadata.skill_id
    with pytest.raises(SkillStateError):
        registry.reserve(skill_id, actor=_actor())
    registry.apply(skill_id, SkillAction.INSTALL, actor=_actor())
    enabled = registry.apply(skill_id, SkillAction.ENABLE, actor=_actor())
    assert enabled.metadata.status == SkillStatus.ENABLED
    reserved = registry.reserve(skill_id, actor=_actor())
    with pytest.raises(SkillError) as busy:
        registry.uninstall(skill_id, actor=_actor())
    assert busy.value.code == "SKILL_IN_USE"
    assert reserved.metadata.version == "1.0.0"
    updated_text = _TEXT.replace("version: 1.0.0", "version: 1.1.0").replace("按完成", "按新版本")
    manifest, body = parse_skill_markdown(updated_text)
    version = registry.update(skill_id, manifest, body, actor=_actor())
    assert version.version == "1.1.0"
    assert "按完成" in registry.read_instructions(skill_id, "1.0.0", actor=_actor())
    registry.release(skill_id, actor=_actor())
    removed = registry.uninstall(skill_id, actor=_actor())
    assert removed.metadata.status == SkillStatus.UNINSTALLED
