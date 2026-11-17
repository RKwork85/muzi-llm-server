"""技能目录只暴露已启用技能的元数据。"""

from __future__ import annotations

from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.mcp.service import McpService
from app.mcp.store import MemoryStore
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
            "scope": "system",
            "executable": False,
            "icon": "",
            "display_name": "",
        }
    ]


def test_catalog_is_empty_without_skills_root() -> None:
    app = build_provider_app(make_settings(skills_root=""), FakeProvider())
    with TestClient(app) as client:
        response = client.get("/api/v1/skills")
    assert response.status_code == 200
    assert response.json() == {"skills": []}


_RUNTIME_TEXT = """---
name: brainstorming
description: 用户上传的同名技能
version: 1.0.0
runtime:
  language: python
  entrypoint: scripts/main.py
---
用户版正文。
"""


def _enable(registry: FileSkillRegistry, skill_id: str, actor: SkillActor) -> None:
    registry.apply(skill_id, SkillAction.INSTALL, actor=actor)
    registry.apply(skill_id, SkillAction.ENABLE, actor=actor)


def test_installed_shadows_system_skill_and_marks_executable(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    system_registry = FileSkillRegistry(root, allow_system=True)
    user_registry = FileSkillRegistry(root)

    sys_manifest, sys_body = parse_skill_markdown(_TEXT)
    sys_skill = system_registry.register(
        sys_manifest,
        sys_body,
        actor=SkillActor(),
        source=SkillSource(kind=SkillSourceKind.LOCAL_DIRECTORY, label="system"),
        scope=SkillScope.SYSTEM,
    )
    _enable(system_registry, sys_skill.metadata.skill_id, SkillActor())

    user_manifest, user_body = parse_skill_markdown(_RUNTIME_TEXT)
    user_skill = user_registry.register(
        user_manifest,
        user_body,
        actor=SkillActor(user_id="user-1"),
        source=SkillSource(kind=SkillSourceKind.LOCAL_DIRECTORY, label="user"),
        scope=SkillScope.USER,
    )
    _enable(user_registry, user_skill.metadata.skill_id, SkillActor(user_id="user-1"))

    app = build_provider_app(make_settings(skills_root=str(root)), FakeProvider())
    service = McpService(
        make_settings(skills_root=str(root)),
        MemoryStore(),
        httpx.AsyncClient(),
        users={"test-token": "user-1"},
    )
    app.state.mcp_service = service
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/skills/installed",
            headers={"Authorization": "Bearer test-token"},
        )
    assert response.status_code == 200, response.text
    payload = response.json()["skills"]
    assert len(payload) == 1  # 同名去重：user 覆盖 system
    item = payload[0]
    assert item["id"] == user_skill.metadata.skill_id
    assert item["scope"] == "user"
    assert item["executable"] is True


def test_health_reports_mount_and_attachments(tmp_path: Path) -> None:
    app = build_provider_app(make_settings(skills_root=str(tmp_path / "skills")), FakeProvider())
    with TestClient(app) as client:
        response = client.get("/api/v1/skills/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["skills_root_mounted"] is True
    assert payload["runtime_attached"] is True
    assert payload["sandbox_alive"] is True
    assert payload["sandbox_backend"] == "subprocess"


def test_health_reports_unmounted_without_skills_root() -> None:
    app = build_provider_app(make_settings(skills_root=""), FakeProvider())
    with TestClient(app) as client:
        response = client.get("/api/v1/skills/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["skills_root_mounted"] is False
    assert payload["runtime_attached"] is False
