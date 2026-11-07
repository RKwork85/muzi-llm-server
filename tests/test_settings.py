"""配置加载与密钥缺省行为。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import safe_validation_text
from app.core.exceptions import ConfigurationError
from app.providers.base import ProviderChatRequest
from app.providers.deepseek import DeepSeekProvider
from tests.conftest import make_settings


def test_settings_reads_explicit_key() -> None:
    settings = make_settings(deepseek_api_key="sk-present-value")
    assert settings.has_api_key() is True
    assert settings.deepseek_model == "deepseek-flash"
    assert "sk-present-value" not in repr(settings)


def test_missing_key_does_not_echo_configuration() -> None:
    settings = make_settings(deepseek_api_key="")
    assert settings.has_api_key() is False
    assert "DEEPSEEK_API_KEY" not in repr(settings)


def test_require_key_on_startup_rejects_empty_key() -> None:
    with pytest.raises(ValidationError) as caught:
        make_settings(require_api_key_on_startup=True, deepseek_api_key="")
    text = safe_validation_text(caught.value)
    assert "DEEPSEEK_API_KEY 未配置" in text
    assert "sk-" not in text


def test_validation_text_strips_secret_inputs() -> None:
    secret = "sk-super-secret-value"
    with pytest.raises(ValidationError) as caught:
        make_settings(
            deepseek_api_key=secret,
            max_tool_rounds=4,
            tool_round_hard_limit=2,
        )
    assert secret not in safe_validation_text(caught.value)


def test_base_url_rejects_resource_path() -> None:
    with pytest.raises(ValidationError):
        make_settings(deepseek_base_url="https://api.deepseek.com/chat/completions")
    settings = make_settings(deepseek_base_url="https://api.deepseek.com/")
    assert settings.deepseek_base_url == "https://api.deepseek.com"


def test_default_model_must_be_allowlisted() -> None:
    with pytest.raises(ValidationError):
        make_settings(deepseek_model="deepseek-chat")


@pytest.mark.asyncio
async def test_provider_without_key_fails_on_request() -> None:
    settings = make_settings(deepseek_api_key="")
    provider = DeepSeekProvider(settings)
    await provider.startup()
    with pytest.raises(ConfigurationError) as caught:
        await provider.create_chat_completion(
            ProviderChatRequest(model="deepseek-flash", messages=[{"role": "user", "content": "hi"}])
        )
    assert caught.value.code == "CONFIGURATION_ERROR"
    assert "sk-" not in caught.value.message
    await provider.aclose()
