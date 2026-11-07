"""密码、刷新凭证、连接串和配置不泄漏密钥。"""

from __future__ import annotations

from urllib.parse import quote_plus

import pytest
from pydantic import ValidationError

from app.core.config import safe_validation_text
from app.core.database.urls import build_async_postgres_url
from app.core.security import (
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
    verify_refresh_token,
)
from tests.conftest import make_settings


def test_password_hash_uses_argon2id() -> None:
    password = "correct-horse-battery"
    stored = hash_password(password)
    assert stored.startswith("$argon2id$")
    assert password not in stored
    assert verify_password(stored, password) is True
    assert verify_password(stored, "wrong-horse-battery") is False
    assert verify_password("not-a-hash", password) is False


def test_empty_password_is_rejected() -> None:
    with pytest.raises(ValueError):
        hash_password("short")


def test_refresh_token_stores_only_hash() -> None:
    raw, digest = generate_refresh_token()
    assert raw not in digest
    assert digest == hash_refresh_token(raw)
    assert len(digest) == 64
    assert verify_refresh_token(digest, raw) is True
    assert verify_refresh_token(digest, raw + "x") is False


def test_database_secrets_stay_out_of_repr() -> None:
    settings = make_settings(
        postgres_password="postgres-secret-value",
        redis_password="redis-secret-value",
    )
    rendered = repr(settings)
    assert "postgres-secret-value" not in rendered
    assert "redis-secret-value" not in rendered


def test_startup_requires_long_passwords() -> None:
    with pytest.raises(ValidationError) as caught:
        make_settings(database_startup_required=True, postgres_password="", redis_password="")
    text = safe_validation_text(caught.value)
    assert "POSTGRES_PASSWORD 未配置或过短" in text
    assert "sk-test-not-real" not in text


def test_postgres_url_quotes_password() -> None:
    password = "p@ss:word"
    settings = make_settings(postgres_password=password, postgres_user="muzi_admin")
    url = build_async_postgres_url(settings)
    assert quote_plus(password) in url
    assert password not in url
    assert url.startswith("postgresql+asyncpg://")


def test_database_host_rejects_credentials() -> None:
    with pytest.raises(ValidationError):
        make_settings(postgres_host="muzi_admin:secret@127.0.0.1")
