"""未要求连接时不占用数据库；连接失败时不把密码写进异常。"""

from __future__ import annotations

import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from app.core.database.redis import close_redis, get_redis_or_none
from app.core.database.session import dispose_engine, get_engine_or_none
from app.core.logging import setup_logging
from app.main import create_app
from tests.conftest import make_settings
from tests.fakes import FakeProvider


def test_log_redacts_database_password(caplog: pytest.LogCaptureFixture) -> None:
    secret = "postgres-secret-value"
    settings = make_settings(postgres_password=secret, redis_password="redis-secret-value")
    setup_logging(settings)
    caplog.set_level(logging.INFO, logger="qs_llm")
    logging.getLogger("qs_llm").info("dsn %s", secret)
    assert secret not in caplog.text


def test_lifespan_skips_database_when_not_required() -> None:
    settings = make_settings()
    app = create_app(settings=settings, provider=FakeProvider())
    with TestClient(app):
        assert app.state.database_ready is False
        assert app.state.redis_ready is False
    assert get_engine_or_none() is None
    assert get_redis_or_none() is None


def test_required_startup_failure_does_not_leak_password() -> None:
    postgres_password = "postgres-secret-value"
    redis_password = "redis-secret-value"
    settings = make_settings(
        database_startup_required=True,
        postgres_password=postgres_password,
        redis_password=redis_password,
        postgres_port=1,
        redis_port=1,
        db_startup_attempts=1,
        db_startup_retry_seconds=0,
        db_connect_timeout_seconds=1,
        redis_socket_timeout_seconds=1,
    )
    app = create_app(settings=settings, provider=FakeProvider())
    try:
        with pytest.raises(Exception) as caught:
            with TestClient(app):
                pass
        rendered = _exception_text(caught.value)
        assert postgres_password not in rendered
        assert redis_password not in rendered
    finally:
        asyncio.run(_cleanup())


async def _cleanup() -> None:
    await close_redis()
    await dispose_engine()


def _exception_text(exc: BaseException) -> str:
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(str(current))
        current = current.__cause__ or current.__context__
    return " ".join(parts)
