"""M4 运行时：快照、生命周期、上下文和路径。"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from app.skills.activation_store import ActivationStore
from app.skills.context_builder import BOUNDARY, ContextBuilder
from app.skills.discovery_models import DiscoveryMode
from app.skills.errors import SkillError, SkillStateError
from app.skills.frontmatter import parse_skill_markdown
from app.skills.models import SkillAction, SkillActor, SkillScope, SkillSource, SkillSourceKind
from app.skills.orchestrator import SkillOrchestrator
from app.skills.registry import FileSkillRegistry
from app.skills.resources import read_resource
from app.skills.runtime_state import RuntimePhase, move

_WEEKLY = """---
name: weekly-report
description: 把本周事项整理成周报
---
按完成、进行中和阻塞来写。不要执行脚本。
"""

_OTHER = """---
name: meeting-notes
description: 整理会议纪要和待办
---
会议结论写在前面。
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
    actor = SkillActor(user_id=user_id)
    registry.apply(skill.metadata.skill_id, SkillAction.INSTALL, actor=actor)
    return registry.apply(skill.metadata.skill_id, SkillAction.ENABLE, actor=actor)


def _service(tmp_path: Path) -> tuple[FileSkillRegistry, SkillOrchestrator]:
    registry = FileSkillRegistry(tmp_path / "skills")
    orchestrator = SkillOrchestrator(registry, ActivationStore(tmp_path / "runtime"))
    return registry, orchestrator


def test_runtime_pins_version_and_survives_update(tmp_path: Path) -> None:
    registry, orchestrator = _service(tmp_path)
    skill = _enable(registry, _WEEKLY)
    package = tmp_path / "skills" / "packages" / skill.metadata.skill_id / "1.0.0"
    (package / "scripts").mkdir()
    (package / "scripts" / "run.py").write_text("open('ran.txt','w').write('x')\n", encoding="utf-8")
    (package / "references").mkdir()
    (package / "references" / "guide.md").write_text("引用正文", encoding="utf-8")
    result = orchestrator.prepare(
        principal_id="user-1",
        task_id="task-1",
        task_text="请使用 weekly-report",
        surface="chat",
        mode=DiscoveryMode.AUTO,
        system_text="系统指令保持不变",
    )
    assert result.fallback is False
    assert result.versions == ("1.0.0",)
    assert BOUNDARY in result.prompt_block
    assert "系统指令保持不变" not in result.prompt_block
    assert not (package / "ran.txt").exists()
    text, digest, size = read_resource(package, "references/guide.md")
    assert text == "引用正文"
    assert len(digest) == 64
    assert size == len("引用正文".encode())
    with pytest.raises(SkillError):
        read_resource(package, "scripts/run.py")
    updated = _WEEKLY.replace("按完成", "按新版本").replace("name: weekly-report", "name: weekly-report\nversion: 1.1.0")
    manifest, body = parse_skill_markdown(updated)
    registry.update(skill.metadata.skill_id, manifest, body, actor=SkillActor(user_id="user-1"))
    registry.apply(skill.metadata.skill_id, SkillAction.DISABLE, actor=SkillActor(user_id="user-1"))
    snapshot = orchestrator.store.get(result.activation_ids[0], principal_id="user-1")
    assert snapshot.skill_version == "1.0.0"
    assert "按完成" in snapshot.instruction_body
    with pytest.raises(SkillError) as hidden:
        orchestrator.store.get(result.activation_ids[0], principal_id="user-2")
    assert hidden.value.code == "SKILL_NOT_FOUND"
    again = orchestrator.prepare(
        principal_id="user-1",
        task_id="task-1",
        task_text="请使用 weekly-report",
        surface="agent",
        mode=DiscoveryMode.EXPLICIT,
        system_text="系统指令保持不变",
        explicit_names=("weekly-report",),
    )
    assert again.error_code == "SKILL_NOT_AVAILABLE"
    assert orchestrator.store.get(result.activation_ids[0], principal_id="user-1").instruction_body == snapshot.instruction_body


def test_context_keeps_system_text_and_budget(tmp_path: Path) -> None:
    registry, orchestrator = _service(tmp_path)
    _enable(registry, _WEEKLY)
    _enable(registry, _OTHER)
    result = orchestrator.prepare(
        principal_id="user-1",
        task_id="task-multi",
        task_text="请使用 weekly-report 和 meeting-notes",
        surface="agent",
        mode=DiscoveryMode.AUTO,
        system_text="系统安全策略",
    )
    built = ContextBuilder().build(
        system_text="系统安全策略",
        user_task="请使用 weekly-report 和 meeting-notes",
        snapshots=tuple(orchestrator.store.for_task("task-multi", principal_id="user-1")),
    )
    assert built.sections[0].text == "系统安全策略"
    assert built.sections[0].untrusted is False
    assert all(section.untrusted for section in built.sections if section.kind == "skill")
    assert len(result.activation_ids) == 2
    with pytest.raises(SkillError) as budget:
        ContextBuilder(budget_chars=10).build(
            system_text="系统安全策略",
            user_task="任务",
            snapshots=tuple(orchestrator.store.for_task("task-multi", principal_id="user-1")),
        )
    assert budget.value.code == "SKILL_CONTEXT_BUDGET_EXCEEDED"


def test_repeat_activation_is_idempotent_and_concurrent(tmp_path: Path) -> None:
    registry, orchestrator = _service(tmp_path)
    _enable(registry, _WEEKLY)
    kwargs = dict(
        principal_id="user-1",
        task_id="task-same",
        task_text="请使用 weekly-report",
        surface="chat",
        mode=DiscoveryMode.AUTO,
        system_text="系统",
    )
    barrier = threading.Barrier(2)
    found: list[str] = []

    def once() -> None:
        barrier.wait()
        result = orchestrator.prepare(**kwargs)
        found.append(result.activation_ids[0])

    threads = [threading.Thread(target=once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert found[0] == found[1]
    skill = registry.get(orchestrator.store.get(found[0], principal_id="user-1").skill_id, actor=SkillActor(user_id="user-1"))
    assert skill.active_count == 1
    cancelled = orchestrator.cancel("task-same", principal_id="user-1")
    assert cancelled[0].status == RuntimePhase.CANCELLED
    assert orchestrator.cancel("task-same", principal_id="user-1") == []
    with pytest.raises(SkillStateError):
        move(RuntimePhase.CANCELLED, "complete")
    with pytest.raises(SkillStateError):
        move(RuntimePhase.COMPLETED, "activate")


def test_resource_paths_cannot_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = tmp_path / "package"
    (package / "references").mkdir(parents=True)
    (package / "references" / "guide.md").write_text("ok", encoding="utf-8")
    (package / "templates").mkdir()
    (package / "templates" / "page.md").write_text("tpl", encoding="utf-8")
    outside = tmp_path / "secret.md"
    outside.write_text("secret", encoding="utf-8")
    for raw in ("../secret.md", "/etc/passwd", "C:/Windows/system.ini", "\\\\server\\share\\a.md", "references\\..\\secret.md"):
        with pytest.raises(SkillError):
            read_resource(package, raw)
    assert read_resource(package, "templates/page.md")[0] == "tpl"
    link = package / "references" / "link.md"
    try:
        os.symlink(outside, link)
    except OSError:
        link.write_text("inside", encoding="utf-8")
        original = Path.resolve

        def escape(self: Path, *args: object, **kwargs: object) -> Path:
            if self.name == "link.md":
                return original(outside, *args, **kwargs)
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", escape)
    with pytest.raises(SkillError):
        read_resource(package, "references/link.md")


def test_corrupt_package_is_not_injected(tmp_path: Path) -> None:
    registry, orchestrator = _service(tmp_path)
    skill = _enable(registry, _WEEKLY)
    path = tmp_path / "skills" / "packages" / skill.metadata.skill_id / "1.0.0" / "SKILL.md"
    path.write_text("损坏的文件", encoding="utf-8")
    result = orchestrator.prepare(
        principal_id="user-1",
        task_id="task-bad",
        task_text="请使用 weekly-report",
        surface="chat",
        mode=DiscoveryMode.AUTO,
        system_text="系统",
    )
    assert result.fallback is False
    assert result.error_code == "SKILL_PACKAGE_INVALID"
    assert result.prompt_block == ""
