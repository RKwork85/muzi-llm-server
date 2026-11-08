"""技能包（bundle）API：上传、列表、卸载与鉴权。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.mcp.service import McpService
from app.mcp.store import MemoryStore
from tests.conftest import build_provider_app, make_settings
from tests.fakes import FakeProvider


def _skill_text(name: str, desc: str) -> str:
    return f"""---
name: {name}
description: {desc}
version: 1.0.0
---
这是 {name} 的正文。
"""


def _zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("skills/alpha/SKILL.md", _skill_text("alpha", "第一个"))
        archive.writestr("skills/beta/SKILL.md", _skill_text("beta", "第二个"))
    return buf.getvalue()


def _app(tmp_path: Path):
    settings = make_settings(skills_root=str(tmp_path / "skills"))
    app = build_provider_app(settings, FakeProvider())
    service = McpService(
        settings,
        MemoryStore(),
        httpx.AsyncClient(),
        users={"test-token": "user-1"},
    )
    app.state.mcp_service = service
    return app


def test_bundle_upload_list_and_uninstall(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer test-token"}
        assert client.get("/api/v1/skills/bundles", headers=headers).json() == {"bundles": []}
        response = client.post(
            "/api/v1/skills/bundles",
            files={"file": ("superpowers.zip", _zip(), "application/zip")},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["name"] == "superpowers"
        assert body["skill_count"] == 2
        assert {item["name"] for item in body["skills"]} == {"alpha", "beta"}
        bundle_id = body["bundle_id"]
        listed = client.get("/api/v1/skills/bundles", headers=headers).json()
        assert len(listed["bundles"]) == 1
        assert listed["bundles"][0]["bundle_id"] == bundle_id
        removed = client.delete(f"/api/v1/skills/bundles/{bundle_id}", headers=headers)
        assert removed.status_code == 200, removed.text
        assert client.get("/api/v1/skills/bundles", headers=headers).json() == {"bundles": []}


def test_bundle_upload_requires_login(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/skills/bundles",
            files={"file": ("x.zip", _zip(), "application/zip")},
        )
        assert response.status_code == 401


def test_bundle_rejects_invalid_zip(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer test-token"}
        response = client.post(
            "/api/v1/skills/bundles",
            files={"file": ("bad.zip", b"not-a-zip", "application/zip")},
            headers=headers,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "SKILL_PACKAGE_INVALID"
