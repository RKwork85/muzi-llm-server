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
        # 显式留空：本机 .env 里的真实密钥会经 load_dotenv 进入 os.environ，
        # 不写死这一项，「未配密钥」用例会真的出网调用方舟，变成环境相关失败。
        "ark_api_key": "",
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
        # 显式关闭：某些用例会经 load_dotenv 把本机 .env 读进进程环境变量，
        # 不写死这一项，后续用例会突然要求登录（401），与用例本身无关。
        "database_startup_required": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture(autouse=True)
def _isolate_prompt_settings(tmp_path):
    """提示词开关是落盘的全局状态，用例必须自带一份空开关。

    否则本机 / CI 上有人把面板里的提示词全关掉后，所有人设、插件说明和工具
    schema 都会消失，一批与开关无关的用例会莫名其妙地红。
    """
    from app.prompts import settings as prompt_settings

    original = prompt_settings._PATH
    prompt_settings.reset_for_tests(tmp_path / "prompt_settings.json")
    try:
        yield
    finally:
        prompt_settings.reset_for_tests(original)


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
