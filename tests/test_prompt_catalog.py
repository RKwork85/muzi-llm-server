"""内置提示词目录与全局开关。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.exceptions import PromptNotFoundError
from app.prompts import settings
from app.prompts.catalog import build_catalog
from app.prompts.skills import skill_notes
from app.services.prompt_service import PromptService


@pytest.fixture()
def prompt_state(tmp_path: Path):
    original = settings._PATH
    settings.reset_for_tests(tmp_path / "prompt_settings.json")
    try:
        yield settings
    finally:
        settings.reset_for_tests(original)


def _item(prompt_id: str):
    for group in build_catalog():
        for item in group.items:
            if item.id == prompt_id:
                return item
    raise AssertionError(f"missing prompt {prompt_id}")


def _items():
    return [item for group in build_catalog() for item in group.items]


def test_catalog_covers_all_sources(prompt_state) -> None:
    groups = {group.id: group for group in build_catalog()}
    assert list(groups) == ["persona", "mode", "plugin", "guard", "tool"]
    assert _item("persona:assistant").content.strip()
    assert _item("mode:plan_mode").content
    assert _item("plugin:pdf").enabled is True
    assert _item("tool:web_search").enabled is True
    assert _item("guard:untrusted").content


def test_disable_persona_falls_back(prompt_state) -> None:
    service = PromptService()
    prompt_state.set_enabled("persona:assistant", False)
    with pytest.raises(PromptNotFoundError):
        service.render("assistant")
    assert "默认助手" in service.render_first(["assistant", "default"])


def test_disable_plugin_note(prompt_state) -> None:
    assert "PDF 文档处理" in skill_notes(["pdf"])
    prompt_state.set_enabled("plugin:pdf", False)
    assert skill_notes(["pdf"]) == ""


def test_disable_tool_prompt_hides_schema(prompt_state) -> None:
    from app.core.config import Settings
    from app.services.tool_service import ToolService
    from app.tools.registry import build_agent_registry

    prompt_state.set_enabled("tool:web_search", False)
    service = ToolService(build_agent_registry(), Settings())
    names = [schema["function"]["name"] for schema in service.schemas_for(None)]
    assert "web_search" not in names
    assert "calculator" in names
    assert _item("tool:web_search").enabled is False


def test_every_registered_tool_is_toggleable(prompt_state) -> None:
    """注册表里的每个工具都要在目录里有开关，否则面板之外还藏着一批常驻 token。"""
    from app.tools.registry import build_agent_registry

    catalogued = {item.id for item in _items() if item.category == "tool"}
    missing = [
        f"tool:{spec.name}"
        for spec in build_agent_registry().all_tools()
        if f"tool:{spec.name}" not in catalogued
    ]
    assert missing == []


def test_prompt_management_has_its_own_permission() -> None:
    """提示词开关必须是 80 级管理码，不能借用页面浏览码。

    ``admin.access`` 只是「能进管理后台」的浏览权限（等级 20，可勾给访客），
    用它当写门禁等于「能看后台」就「能关掉所有人的系统提示词」。
    """
    from app.authorization.domain.catalog import PERMISSION_BY_CODE, PERMISSION_MIN_RANK, ROLE_BY_CODE
    from app.authorization.domain.pages import PAGE_BY_KEY

    assert "prompt.manage" in PERMISSION_BY_CODE
    assert PERMISSION_MIN_RANK["prompt.manage"] == 80
    assert "prompt.manage" in ROLE_BY_CODE["admin"].permissions
    assert "prompt.manage" in PAGE_BY_KEY["admin"].features
    for role in ("guest", "member", "developer"):
        assert "prompt.manage" not in ROLE_BY_CODE[role].permissions


def test_prompt_endpoints_do_not_gate_on_page_access() -> None:
    """接口层不得用 admin.access 作为门禁，否则低等级角色能全局关提示词。"""
    path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "endpoints" / "prompts.py"
    text = path.read_text(encoding="utf-8")
    assert 'require_permission("admin.access")' not in text
    assert text.count('require_permission("prompt.manage")') == 1
    # 读与写共用同一个依赖别名，避免出现「读开放、写收窄」的不一致。
    assert "PromptManager" in text


def test_external_file_edit_is_picked_up(prompt_state, tmp_path: Path) -> None:
    path = tmp_path / "prompt_settings.json"
    assert _item("plugin:word").enabled is True
    path.write_text('{"disabled": ["plugin:word"]}', encoding="utf-8")
    assert _item("plugin:word").enabled is False
