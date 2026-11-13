"""M3 发现：只读元数据、隔离、排序和选择后才加载。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.prompts.skills import skill_notes
from app.skills.discovery import SkillDiscoveryService
from app.skills.discovery_models import DecisionKind, DiscoveryMode
from app.skills.errors import SkillError
from app.skills.frontmatter import parse_skill_markdown
from app.skills.loader import SkillPackageLoader
from app.skills.models import SkillAction, SkillActor, SkillScope, SkillSource, SkillSourceKind
from app.skills.registry import FileSkillRegistry

_WEEKLY = """---
name: weekly-report
description: 把本周事项整理成周报
metadata:
  category: writing
  tags: weekly
---
按完成、进行中和阻塞来写。
"""

_ALPHA = """---
name: alpha-notes
description: 整理周报素材
---
甲的说明。
"""

_BETA = """---
name: beta-notes
description: 整理周报素材
---
乙的说明。
"""


def _enable(registry: FileSkillRegistry, text: str, user_id: str = "user-1"):
    manifest, body = parse_skill_markdown(text)
    skill = registry.register(
        manifest,
        body,
        actor=SkillActor(user_id=user_id),
        source=SkillSource(kind=SkillSourceKind.LOCAL_DIRECTORY, label="local"),
        scope=SkillScope.USER,
    )
    skill_id = skill.metadata.skill_id
    registry.apply(skill_id, SkillAction.INSTALL, actor=SkillActor(user_id=user_id))
    return registry.apply(skill_id, SkillAction.ENABLE, actor=SkillActor(user_id=user_id))


def test_discovery_does_not_read_skill_bodies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = FileSkillRegistry(tmp_path)
    _enable(registry, _WEEKLY)
    reads: list[str] = []
    original = Path.read_bytes

    def spy(self: Path, *args: object, **kwargs: object) -> bytes:
        if self.name == "SKILL.md":
            reads.append(str(self))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", spy)
    outcome = SkillDiscoveryService(registry).discover(
        actor=SkillActor(user_id="user-1"),
        task_text="请使用 weekly-report",
        mode=DiscoveryMode.AUTO,
    )
    assert outcome.decision == DecisionKind.SELECT
    assert outcome.candidates[0].reason_codes[0] == "name_exact"
    assert reads == []
    SkillPackageLoader(registry).load(
        outcome.selected_ids[0],
        outcome.candidates[0].version,
        actor=SkillActor(user_id="user-1"),
    )
    assert len(reads) == 1


def test_modes_isolation_and_stable_order(tmp_path: Path) -> None:
    registry = FileSkillRegistry(tmp_path)
    alpha = _enable(registry, _ALPHA)
    beta = _enable(registry, _BETA)
    hidden = _enable(registry, _WEEKLY, user_id="user-2")
    service = SkillDiscoveryService(registry)
    actor = SkillActor(user_id="user-1")
    empty = service.discover(actor=actor, task_text="明天吃什么", mode=DiscoveryMode.AUTO)
    assert empty.decision == DecisionKind.NONE
    assert empty.candidates == ()
    first = service.discover(actor=actor, task_text="整理周报素材", mode=DiscoveryMode.AUTO)
    second = service.discover(actor=actor, task_text="整理周报素材", mode=DiscoveryMode.AUTO)
    assert [item.name for item in first.candidates] == ["alpha-notes", "beta-notes"]
    assert [item.skill_id for item in first.candidates] == [item.skill_id for item in second.candidates]
    assert first.decision == DecisionKind.RECOMMEND
    assert first.selected_ids == ()
    assert "description" in first.candidates[0].reason_codes
    recommended = service.discover(actor=actor, task_text="请使用 alpha-notes", mode=DiscoveryMode.RECOMMENDED)
    assert recommended.decision == DecisionKind.RECOMMEND
    assert recommended.selected_ids == ()
    disabled = service.discover(actor=actor, task_text="请使用 weekly-report", mode=DiscoveryMode.DISABLED)
    assert disabled.candidates == ()
    explicit = service.discover(
        actor=actor,
        task_text="无关内容",
        mode=DiscoveryMode.EXPLICIT,
        explicit_skill_ids=(alpha.metadata.skill_id,),
    )
    assert explicit.decision == DecisionKind.EXPLICIT
    assert explicit.selected_ids == (alpha.metadata.skill_id,)
    missing = service.discover(
        actor=actor,
        task_text="无关内容",
        mode=DiscoveryMode.EXPLICIT,
        explicit_skill_ids=(hidden.metadata.skill_id,),
    )
    assert missing.error_code == "SKILL_NOT_FOUND"
    assert beta.metadata.skill_id != hidden.metadata.skill_id


def test_disabled_skill_is_not_selected_and_load_rechecks(tmp_path: Path) -> None:
    registry = FileSkillRegistry(tmp_path)
    skill = _enable(registry, _WEEKLY)
    actor = SkillActor(user_id="user-1")
    registry.apply(skill.metadata.skill_id, SkillAction.DISABLE, actor=actor)
    outcome = SkillDiscoveryService(registry).discover(
        actor=actor,
        task_text="请使用 weekly-report",
        mode=DiscoveryMode.AUTO,
    )
    assert outcome.selected_ids == ()
    refused = SkillDiscoveryService(registry).discover(
        actor=actor,
        task_text="x",
        mode=DiscoveryMode.EXPLICIT,
        explicit_names=("weekly-report",),
    )
    assert refused.error_code == "SKILL_NOT_AVAILABLE"
    with pytest.raises(SkillError) as error:
        SkillPackageLoader(registry).load(skill.metadata.skill_id, "1.0.0", actor=actor)
    assert error.value.code == "SKILL_NOT_AVAILABLE"


def test_legacy_prompts_stay_in_place() -> None:
    """四条旧提示仍由 skill_notes 提供。v1.8 只把标题扩成插件/技能，正文和执行边界不变。"""
    from app.prompts.skills import SKILL_INSTRUCTIONS

    assert SKILL_INSTRUCTIONS["research"].startswith("深度研究：")
    assert SKILL_INSTRUCTIONS["polish"].startswith("文案润色：")
    assert SKILL_INSTRUCTIONS["minutes"].startswith("会议纪要：")
    assert SKILL_INSTRUCTIONS["weekly"].startswith("周报生成：")
    executing = skill_notes(["research", "polish", "minutes", "weekly"], execute=True)
    assert executing.startswith("已启用插件/技能。按这些做法完成任务：")
    assert "深度研究：" in executing
    assert "文案润色：" in executing
    assert "会议纪要：" in executing
    assert "周报生成：" in executing
    planning = skill_notes(["research"], execute=False)
    assert planning.startswith("这些插件/技能只作为计划参考，不要执行其中的动作：")
    assert "深度研究" in planning
    assert skill_notes(["unknown"], execute=True) == ""
