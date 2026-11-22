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


def test_external_file_edit_is_picked_up(prompt_state, tmp_path: Path) -> None:
    path = tmp_path / "prompt_settings.json"
    assert _item("plugin:word").enabled is True
    path.write_text('{"disabled": ["plugin:word"]}', encoding="utf-8")
    assert _item("plugin:word").enabled is False
