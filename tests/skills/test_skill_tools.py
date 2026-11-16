"""运行时技能工具：模型通过 discover_skills/load_skill/read_skill_resource 自主取用技能。"""

from __future__ import annotations

import json
from pathlib import Path

from app.skills.activation_store import ActivationStore
from app.skills.adapters import SkillPromptAdapter
from app.skills.frontmatter import parse_skill_markdown
from app.skills.models import SkillAction, SkillActor, SkillScope, SkillSource, SkillSourceKind
from app.skills.orchestrator import SkillOrchestrator
from app.skills.registry import FileSkillRegistry
from app.skills.tools import (
    CURRENT_SKILL_PRINCIPAL,
    CURRENT_SKILL_RUNTIME,
    DiscoverSkillsArgs,
    ExecuteSkillScriptArgs,
    LoadSkillArgs,
    ReadSkillResourceArgs,
    discover_skills,
    execute_skill_script,
    load_skill,
    read_skill_resource,
)

_TEXT = """---
name: weekly-report
description: 整理周报
version: 1.0.0
metadata:
  category: writing
---
按完成、进行中和阻塞来写。
"""


def _runtime(tmp_path: Path) -> tuple[SkillPromptAdapter, FileSkillRegistry, str]:
    registry = FileSkillRegistry(tmp_path / "skills", allow_system=True)
    orchestrator = SkillOrchestrator(registry, ActivationStore(tmp_path / "runtime"))
    runtime = SkillPromptAdapter(orchestrator)
    actor = SkillActor()
    manifest, body = parse_skill_markdown(_TEXT)
    skill = registry.register(
        manifest,
        body,
        actor=actor,
        source=SkillSource(kind=SkillSourceKind.LOCAL_DIRECTORY, label="local"),
        scope=SkillScope.SYSTEM,
    )
    registry.apply(skill.metadata.skill_id, SkillAction.INSTALL, actor=actor)
    registry.apply(skill.metadata.skill_id, SkillAction.ENABLE, actor=actor)
    package = registry.package_dir(skill.metadata.skill_id, "1.0.0", actor=actor)
    (package / "references").mkdir(parents=True, exist_ok=True)
    (package / "references" / "guide.md").write_text("参考正文", encoding="utf-8")
    return runtime, registry, skill.metadata.skill_id


def test_skill_tools_discover_load_and_read(tmp_path: Path) -> None:
    runtime, _registry, skill_id = _runtime(tmp_path)
    runtime_token = CURRENT_SKILL_RUNTIME.set(runtime)
    principal_token = CURRENT_SKILL_PRINCIPAL.set("user-1")
    try:
        discovered = json.loads(discover_skills(DiscoverSkillsArgs()))
        assert discovered["count"] >= 1
        assert "weekly-report" in {item["name"] for item in discovered["skills"]}
        filtered = json.loads(discover_skills(DiscoverSkillsArgs(query="周报")))
        assert filtered["count"] == 1

        loaded = json.loads(load_skill(LoadSkillArgs(skill_id=skill_id)))
        assert "按完成" in loaded["instructions"]
        assert loaded["name"] == "weekly-report"

        resource = json.loads(
            read_skill_resource(ReadSkillResourceArgs(skill_id=skill_id, path="references/guide.md"))
        )
        assert resource["content"] == "参考正文"
    finally:
        CURRENT_SKILL_RUNTIME.reset(runtime_token)
        CURRENT_SKILL_PRINCIPAL.reset(principal_token)


def test_skill_tools_without_runtime_return_error(tmp_path: Path) -> None:
    output = json.loads(discover_skills(DiscoverSkillsArgs()))
    assert output["error"]["code"] == "SKILL_RUNTIME_UNAVAILABLE"


_RUNTIME_SKILL = """---
name: data-analysis
description: 分析数据并生成结果
version: 1.0.0
runtime:
  language: python
  entrypoint: scripts/main.py
  timeout: 10
---
分析数据。
"""

_SCRIPT = (
    "import json\n"
    "args = json.load(open('args.json', encoding='utf-8'))\n"
    "print('hello', args.get('name', 'world'))\n"
    "print(6 * 7)\n"
)


def _executable_runtime(tmp_path: Path) -> tuple[SkillPromptAdapter, FileSkillRegistry, str]:
    registry = FileSkillRegistry(tmp_path / "skills", allow_system=True)
    orchestrator = SkillOrchestrator(registry, ActivationStore(tmp_path / "runtime"))
    runtime = SkillPromptAdapter(orchestrator)
    actor = SkillActor()
    manifest, body = parse_skill_markdown(_RUNTIME_SKILL)
    skill = registry.register(
        manifest,
        body,
        actor=actor,
        source=SkillSource(kind=SkillSourceKind.LOCAL_DIRECTORY, label="local"),
        scope=SkillScope.SYSTEM,
    )
    registry.apply(skill.metadata.skill_id, SkillAction.INSTALL, actor=actor)
    registry.apply(skill.metadata.skill_id, SkillAction.ENABLE, actor=actor)
    package = registry.package_dir(skill.metadata.skill_id, "1.0.0", actor=actor)
    (package / "scripts").mkdir(parents=True, exist_ok=True)
    (package / "scripts" / "main.py").write_text(_SCRIPT, encoding="utf-8")
    return runtime, registry, skill.metadata.skill_id


def test_execute_skill_script_runs_entrypoint(tmp_path: Path) -> None:
    runtime, _registry, skill_id = _executable_runtime(tmp_path)
    runtime_token = CURRENT_SKILL_RUNTIME.set(runtime)
    principal_token = CURRENT_SKILL_PRINCIPAL.set("user-1")
    try:
        result = json.loads(
            execute_skill_script(ExecuteSkillScriptArgs(skill_id=skill_id, args={"name": "muzi"}))
        )
        assert result["exit_code"] == 0
        assert "hello muzi" in result["output"]
        assert "42" in result["output"]
        assert "muzi_skill_" not in result["output"]
    finally:
        CURRENT_SKILL_RUNTIME.reset(runtime_token)
        CURRENT_SKILL_PRINCIPAL.reset(principal_token)


def test_execute_skill_script_rejects_non_executable(tmp_path: Path) -> None:
    runtime, _registry, skill_id = _runtime(tmp_path)
    runtime_token = CURRENT_SKILL_RUNTIME.set(runtime)
    principal_token = CURRENT_SKILL_PRINCIPAL.set("user-1")
    try:
        result = json.loads(execute_skill_script(ExecuteSkillScriptArgs(skill_id=skill_id)))
        assert result["error"]["code"] == "SKILL_NOT_EXECUTABLE"
    finally:
        CURRENT_SKILL_RUNTIME.reset(runtime_token)
        CURRENT_SKILL_PRINCIPAL.reset(principal_token)
