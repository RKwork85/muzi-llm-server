"""共享测试配置。显式传入全部字段，避免机器上的环境变量干扰。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.providers.base import LLMProvider
from tests.fakes import FakeProvider


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "deepseek_api_key": "sk-test-not-real",
        "deepseek_base_url": "https://api.deepseek.com",
        "deepseek_model": "deepseek-flash",
        "deepseek_model_allowlist": "deepseek-flash,deepseek-v4-pro",
        "deepseek_timeout_seconds": 5,
        "deepseek_max_retries": 2,
        "deepseek_retry_backoff_seconds": 0,
        "deepseek_thinking": "disabled",
        "log_level": "INFO",
        "require_api_key_on_startup": False,
        "expose_reasoning": False,
        "max_tool_rounds": 3,
        "tool_round_hard_limit": 5,
        "tool_timeout_seconds": 0.2,
        "tool_result_max_chars": 1000,
        "llm_provider": "deepseek",
        "app_name": "DeepSeek FastAPI Phase 1",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def app(settings: Settings, provider: FakeProvider):
    return create_app(settings=settings, provider=provider)


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def build_provider_app(settings: Settings, provider: LLMProvider):
    return create_app(settings=settings, provider=provider)
