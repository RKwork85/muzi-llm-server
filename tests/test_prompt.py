"""提示词注册表。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.exceptions import PromptNotFoundError, PromptVariableError
from app.services.prompt_service import PromptService
from tests.fakes import FakeProvider, chat_payload, make_result


def test_default_prompt_renders_without_variables() -> None:
    text = PromptService().render("default", {})
    assert "默认助手" in text
    assert "sk-" not in text
    assert "{{" not in text


def test_named_template_and_missing_variable() -> None:
    service = PromptService()
    rendered = service.render("json_assistant", {"task": "总结要点"})
    assert "总结要点" in rendered
    assert "json" in rendered.lower()
    with pytest.raises(PromptVariableError) as caught:
        service.render("json_assistant", {})
    assert caught.value.code == "PROMPT_VARIABLE_MISSING"
    assert "task" in caught.value.message


def test_plan_mode_prompt_is_loaded() -> None:
    service = PromptService()
    text = service.render("plan_mode", {})
    assert "你当前处于 Plan 模式" in text
    assert "工作权限" in text
    assert "{{" not in text
    assert "plan_mode" in service.names()


def test_plan_mode_prompt_is_injected_only_for_plan() -> None:
    from app.services.chat_service import ChatService, _mode_prompt

    service = object.__new__(ChatService)
    service.prompts = PromptService()
    plan_text = _mode_prompt("plan", [], service._plan_instructions())
    assert "你当前处于 Plan 模式" in plan_text
    assert "只读" in plan_text
    # Ask 与 Agent 不应拼上 Plan 的说明
    assert _mode_prompt("ask", [], service._plan_instructions()) == ""


def test_plan_prompt_falls_back_when_file_missing(tmp_path: Path) -> None:
    from app.services.chat_service import ChatService, _mode_prompt

    service = object.__new__(ChatService)
    service.prompts = PromptService(tmp_path)
    assert service._plan_instructions() == ""
    fallback = _mode_prompt("plan", [], service._plan_instructions())
    assert "你处于 Plan 模式" in fallback


def test_unknown_prompt_name() -> None:
    with pytest.raises(PromptNotFoundError):
        PromptService().render("missing_prompt", {})


def test_prompt_files_do_not_contain_secrets() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "prompts"
    paths = list(root.rglob("*.txt")) + list(root.rglob("*.md"))
    for path in paths:
        text = path.read_text(encoding="utf-8").lower()
        assert "sk-" not in text, path
        assert "api_key" not in text, path
        assert "authorization" not in text, path


def test_api_selects_prompt_without_hardcoding_it(client, provider: FakeProvider) -> None:
    provider.push(make_result("算完了"))
    response = client.post(
        "/api/v1/chat/completions",
        json=chat_payload(prompt_name="calculator"),
    )
    assert response.status_code == 200
    assert "calculator" in provider.calls[0].messages[0]["content"]


def test_api_reports_missing_prompt_variable(client, provider: FakeProvider) -> None:
    response = client.post(
        "/api/v1/chat/completions",
        json=chat_payload(prompt_name="json_assistant"),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PROMPT_VARIABLE_MISSING"
    assert "task" in response.json()["error"]["message"]
    assert provider.calls == []


def test_api_unknown_prompt(client, provider: FakeProvider) -> None:
    response = client.post(
        "/api/v1/chat/completions",
        json=chat_payload(prompt_name="not_a_prompt"),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PROMPT_NOT_FOUND"
    assert provider.calls == []


def test_prompt_path_traversal_rejected(client, provider: FakeProvider) -> None:
    response = client.post(
        "/api/v1/chat/completions",
        json=chat_payload(prompt_name="../default"),
    )
    assert response.status_code == 422
    assert provider.calls == []
