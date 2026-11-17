"""技能包（bundle）导入、列表与卸载。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from app.skills.errors import SkillError, SkillStateError, SkillValidationError
from app.skills.models import SkillAction, SkillActor, SkillScope, SkillStatus
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
    # 落盘后处于 REGISTERED，等待安全审核通过后启用
    for meta in listed[0].skills:
        assert meta.status == SkillStatus.REGISTERED
        assert meta.bundle_id == bundle.bundle_id
    assert registry.read_instructions(listed[0].skills[0].skill_id, "1.0.0", actor=actor)
    registry.uninstall_bundle(bundle.bundle_id, actor=actor)
    assert registry.list_bundles(actor=actor) == []


def test_bundle_uninstall_is_atomic_when_any_skill_is_busy(tmp_path: Path) -> None:
    """包内任一技能仍在用就整体拒绝，不能留下半卸载的技能包。"""
    registry = FileSkillRegistry(tmp_path)
    actor = SkillActor(user_id="user-1")
    bundle = registry.import_bundle(_bundle_zip(), actor=actor, name="superpowers.zip", scope=SkillScope.USER)
    busy, idle = bundle.skills[0].skill_id, bundle.skills[1].skill_id
    registry.apply(busy, SkillAction.INSTALL, actor=actor)
    registry.apply(busy, SkillAction.ENABLE, actor=actor)
    registry.reserve(busy, actor=actor)
    with pytest.raises(SkillStateError) as error:
        registry.uninstall_bundle(bundle.bundle_id, actor=actor)
    assert error.value.code == "SKILL_IN_USE"
    # 校验失败时一个技能都不落盘，包仍然完整
    assert registry.get(busy, actor=actor).metadata.status == SkillStatus.ENABLED
    assert registry.get(idle, actor=actor).metadata.status == SkillStatus.REGISTERED
    assert len(registry.list_bundles(actor=actor)) == 1


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


def _skill_with_body(name: str, body: str) -> str:
    return f"""---
name: {name}
description: 描述
version: 1.0.0
---
{body}
"""


def _zip_of(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for path, content in members.items():
            archive.writestr(path, content)
    return buf.getvalue()


def test_bundle_rejects_invalid_skill_with_zero_disk_write(tmp_path: Path) -> None:
    """阶段一静态校验失败时整体拒绝，磁盘零写入（无孤儿记录）。"""
    registry = FileSkillRegistry(tmp_path)
    actor = SkillActor(user_id="user-1")
    payload = _zip_of(
        {
            "skills/ok/SKILL.md": _skill_text("ok-skill", "合法"),
            "skills/bad/SKILL.md": "没有清单正文",
        }
    )
    with pytest.raises(SkillValidationError):
        registry.import_bundle(payload, actor=actor, name="mixed.zip", scope=SkillScope.USER)
    # 零写入：records 无 sk_*.json，packages 无残留
    assert list((tmp_path / "records").glob("sk_*.json")) == []
    assert not (tmp_path / "packages").exists() or list((tmp_path / "packages").iterdir()) == []
    assert registry.list_bundles(actor=actor) == []


def test_bundle_accepts_compressed_resources_that_expand_over_1mb(tmp_path: Path) -> None:
    """重资源技能包：压缩后很小但解压后超 1MB，在新上限内应能导入。

    典型是 office 技能内嵌 XSD schema：zip 约 160KB，解压后 1.15MB，
    单个 schema 文件可达 246KB。旧上限 1MB 会误拒。
    """
    registry = FileSkillRegistry(tmp_path)
    actor = SkillActor(user_id="user-1")
    big_schema = b"x" * 300_000  # 300KB，超过旧 256KB 单文件上限
    payload = _zip_of(
        {
            "skills/office/SKILL.md": _skill_text("office", "Office 文档处理"),
            "skills/office/scripts/schemas/a.xsd": big_schema.decode("latin-1"),
            "skills/office/scripts/schemas/b.xsd": ("y" * 900_000),
            "skills/office/scripts/schemas/c.xsd": ("z" * 100_000),
        }
    )
    bundle = registry.import_bundle(payload, actor=actor, name="office.zip", scope=SkillScope.USER)
    assert bundle.skill_count == 1
    assert registry.list_bundles(actor=actor)


def test_bundle_still_rejects_package_over_limit(tmp_path: Path) -> None:
    registry = FileSkillRegistry(tmp_path)
    actor = SkillActor(user_id="user-1")
    # 6MB 解压总量超过 5MB 上限
    payload = _zip_of(
        {
            "skills/big/SKILL.md": _skill_text("big", "超大"),
            "skills/big/blob.bin": "b" * (5_000_001),
        }
    )
    with pytest.raises(SkillValidationError) as error:
        registry.import_bundle(payload, actor=actor, name="big.zip", scope=SkillScope.USER)
    assert error.value.code == "SKILL_PACKAGE_TOO_LARGE"
    assert registry.list_bundles(actor=actor) == []


def test_bundle_imports_from_file_like_stream(tmp_path: Path) -> None:
    """import_bundle 接受 file-like 输入（流式），不再强制 bytes。"""
    registry = FileSkillRegistry(tmp_path)
    actor = SkillActor(user_id="user-1")
    payload = _zip_of({"skills/a/SKILL.md": _skill_text("a", "流式技能")})
    bundle = registry.import_bundle(io.BytesIO(payload), actor=actor, name="stream.zip", scope=SkillScope.USER)
    assert bundle.skill_count == 1
    assert registry.list_bundles(actor=actor)


def test_bundle_can_uninstall_a_rejected_skill(tmp_path: Path) -> None:
    """审核被拒（REJECTED）的技能也应能卸载，不能卡死在「当前状态不能执行该动作」。"""
    registry = FileSkillRegistry(tmp_path)
    actor = SkillActor(user_id="user-1")
    bundle = registry.import_bundle(_bundle_zip(), actor=actor, name="rejected.zip", scope=SkillScope.USER)
    for meta in bundle.skills:
        registry.reject(meta.skill_id, actor=actor)
        assert registry.get(meta.skill_id, actor=actor).metadata.status == SkillStatus.REJECTED
    registry.uninstall_bundle(bundle.bundle_id, actor=actor)
    assert registry.list_bundles(actor=actor) == []


def test_bundle_rejects_duplicate_names_inside_one_bundle(tmp_path: Path) -> None:
    """同一技能包内同名技能在落盘前被拦截，不留孤儿。"""
    registry = FileSkillRegistry(tmp_path)
    actor = SkillActor(user_id="user-1")
    payload = _zip_of(
        {
            "skills/a/SKILL.md": _skill_text("dup", "第一个"),
            "skills/b/SKILL.md": _skill_text("dup", "第二个"),
        }
    )
    with pytest.raises(SkillError) as error:
        registry.import_bundle(payload, actor=actor, name="dup.zip", scope=SkillScope.USER)
    assert error.value.code == "SKILL_NAME_CONFLICT"
    assert list((tmp_path / "records").glob("sk_*.json")) == []
    assert registry.list_bundles(actor=actor) == []
