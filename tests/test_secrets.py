"""日志和响应不泄漏密钥，也不记录用户原文。"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.logging import setup_logging
from tests.conftest import make_settings
from tests.fakes import FakeProvider, chat_payload, make_result


def test_log_filter_redacts_key_and_bearer(caplog) -> None:
    secret = "sk-log-secret-value"
    settings = make_settings(deepseek_api_key=secret)
    setup_logging(settings)
    caplog.set_level(logging.INFO, logger="qs_llm")
    logging.getLogger("qs_llm").info("Authorization: Bearer %s", secret)
    assert secret not in caplog.text
    assert "***" in caplog.text


def test_chat_logs_do_not_include_prompt_or_key(client: TestClient, provider: FakeProvider, caplog, settings) -> None:
    caplog.set_level(logging.INFO, logger="qs_llm")
    marker = "用户隐私句子-TOKEN-7788"
    provider.push(make_result("可以"))
    response = client.post(
        "/api/v1/chat/completions",
        json=chat_payload(messages=[{"role": "user", "content": marker}]),
    )
    assert response.status_code == 200
    assert marker not in caplog.text
    assert settings.deepseek_api_key not in caplog.text
    assert settings.deepseek_api_key not in response.text


def test_example_env_and_readme_have_no_real_key() -> None:
    root = Path(__file__).resolve().parents[1]
    example = (root / ".env.example").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    gitignore = (root / ".gitignore").read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY=" in example
    assert "DEEPSEEK_API_KEY=sk-" not in example
    assert ".env" in gitignore.splitlines()
    assert re.search(r"sk-[A-Za-z0-9]{16,}", example) is None
    assert re.search(r"sk-[A-Za-z0-9]{16,}", readme) is None
