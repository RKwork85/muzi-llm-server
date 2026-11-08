"""技能目录只暴露已启用技能的元数据。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.skills.frontmatter import parse_skill_markdown
from app.skills.models import SkillAction, SkillActor, SkillScope, SkillSource, SkillSourceKind
from app.skills.registry import FileSkillRegistry
from tests.conftest import build_provider_app, make_settings
from tests.fakes import FakeProvider

_TEXT = """---
name: brainstorming
description: 动手之前先把需求问清楚
---
先问清楚要做什么。
"""


def test_catalog_lists_enabled_system_skills(tmp_path: Path) -> None:
    registry = FileSkillRegistry(tmp_path / "skills", allow_system=True)
    manifest, body = parse_skill_markdown(_TEXT)
    actor = SkillActor()
    skill = registry.register(
        manifest,
        body,
        actor=actor,
        source=SkillSource(kind=SkillSourceKind.LOCAL_DIRECTORY, label="superpowers"),
        scope=SkillScope.SYSTEM,
    )
    skill_id = skill.metadata.skill_id
    registry.apply(skill_id, SkillAction.INSTALL, actor=actor)
    registry.apply(skill_id, SkillAction.ENABLE, actor=actor)
    app = build_provider_app(make_settings(skills_root=str(tmp_path / "skills")), FakeProvider())
    with TestClient(app) as client:
        response = client.get("/api/v1/skills")
    assert response.status_code == 200
    payload = response.json()["skills"]
    assert payload == [
        {
            "id": skill_id,
            "name": "brainstorming",
            "description": "动手之前先把需求问清楚",
            "source": "superpowers",
        }
    ]


def test_catalog_is_empty_without_skills_root() -> None:
    app = build_provider_app(make_settings(skills_root=""), FakeProvider())
    with TestClient(app) as client:
        response = client.get("/api/v1/skills")
    assert response.status_code == 200
    assert response.json() == {"skills": []}
