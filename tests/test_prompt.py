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


def test_unknown_prompt_name() -> None:
    with pytest.raises(PromptNotFoundError):
        PromptService().render("missing_prompt", {})


def test_prompt_files_do_not_contain_secrets() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "prompts"
    for path in root.rglob("*.txt"):
        text = path.read_text(encoding="utf-8").lower()
        assert "sk-" not in text
        assert "api_key" not in text
        assert "authorization" not in text


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
