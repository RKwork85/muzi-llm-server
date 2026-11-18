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
    caplog.set_level(logging.INFO, logger="muzi_llm")
    logging.getLogger("muzi_llm").info("dsn %s", secret)
    assert secret not in caplog.text


def test_lifespan_skips_database_when_not_required() -> None:
    settings = make_settings()
    app = create_app(settings=settings, provider=FakeProvider())
    with TestClient(app):
        assert app.state.database_ready is False
        assert app.state.redis_ready is False
    assert get_engine_or_none() is None
    assert get_redis_or_none() is None


_MISSING = object()


async def test_warn_pending_migrations(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """schema 落后/从未初始化时打印醒目警告，已对齐时静默。"""
    from alembic.script import ScriptDirectory

    from app.core.config import ROOT
    from app.core.database import lifecycle

    head = ScriptDirectory(str(ROOT / "alembic")).get_current_head()
    caplog.set_level(logging.WARNING, logger="muzi_llm")

    def set_version(version):
        class Session:
            async def scalar(self, _stmt):
                if version is _MISSING:
                    raise RuntimeError("alembic_version missing")
                return version

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

        monkeypatch.setattr(lifecycle, "get_session_factory", lambda: (lambda: Session()))

    settings = make_settings()

    # 落后 → 警告，且包含当前与目标版本。
    set_version("stale-rev")
    caplog.clear()
    await lifecycle._warn_pending_migrations(settings)
    assert "落后于代码迁移" in caplog.text
    assert "stale-rev" in caplog.text
    assert head in caplog.text

    # 从未初始化（表缺失）→ 提示未初始化。
    set_version(_MISSING)
    caplog.clear()
    await lifecycle._warn_pending_migrations(settings)
    assert "从未初始化" in caplog.text

    # 已对齐 → 静默。
    set_version(head)
    caplog.clear()
    await lifecycle._warn_pending_migrations(settings)
    assert "落后于代码迁移" not in caplog.text


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
