"""Redis、容器绑定、迁移、备份恢复和重启后的数据保留。"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.database.redis import close_redis, get_redis, init_redis
from app.core.database.session import dispose_engine, get_session_factory, init_engine
from app.core.database.urls import build_async_postgres_url
from app.main import create_app
from app.models.tables import User
from tests.fakes import FakeProvider

ROOT = Path(__file__).resolve().parents[3]
BACKUP_DIR = ROOT / "deploy" / "docker" / "backups"
RESTORE_DB = "muzi_llm_restore_check"


@pytest.fixture
async def settings():
    get_settings.cache_clear()
    loaded = Settings()
    init_engine(loaded)
    init_redis(loaded)
    try:
        yield loaded
    finally:
        await close_redis()
        await dispose_engine()


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _wait_healthy(container: str, timeout: float = 90) -> None:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Health.Status}}", container],
            capture_output=True,
            text=True,
        )
        last = result.stdout.strip()
        if last == "healthy":
            return
        time.sleep(2)
    raise AssertionError(f"{container} health={last or 'unknown'}")


def test_containers_are_healthy_local_and_volumed() -> None:
    for container in ("muzi-postgres", "muzi-redis"):
        _wait_healthy(container)
        bindings = _docker("inspect", "-f", "{{json .HostConfig.PortBindings}}", container).stdout
        assert "127.0.0.1" in bindings
        assert "0.0.0.0" not in bindings
    _docker("volume", "inspect", "muzi-postgres-data")
    _docker("volume", "inspect", "muzi-redis-data")
    network = _docker("inspect", "-f", "{{json .NetworkSettings.Networks}}", "muzi-postgres").stdout
    assert "muzi-backend" in network


async def test_redis_auth_ttl_and_persistence(settings: Settings) -> None:
    client = get_redis()
    assert await client.ping() is True
    appendonly = await client.config_get("appendonly")
    save = await client.config_get("save")
    assert appendonly["appendonly"] == "yes"
    assert "900" in save["save"]
    assert "10000" in save["save"]
    key = f"muzi:it:{uuid.uuid4().hex[:8]}"
    await client.set(key, "kept", ex=120)
    assert await client.get(key) == "kept"
    ttl = await client.ttl(key)
    assert 0 < ttl <= 120
    await client.bgsave()
    await asyncio.sleep(1.5)
    _docker("restart", "muzi-redis")
    _wait_healthy("muzi-redis")
    await close_redis()
    init_redis(settings)
    assert await get_redis().get(key) == "kept"
    await get_redis().delete(key)


def test_alembic_current_is_head() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "current"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "(head)" in result.stdout


def test_backup_restore_and_retention() -> None:
    email = f"backup{uuid.uuid4().hex[:10]}@example.com"
    username = "b" + uuid.uuid4().hex[:10]
    asyncio.run(_insert_user(email, username))
    old_dump = BACKUP_DIR / "muzi_llm-20000101-000000.dump"
    old_dump.write_bytes(b"expired")
    expired = time.time() - 40 * 24 * 3600
    os.utime(old_dump, (expired, expired))
    user_id: uuid.UUID | None = None
    try:
        backup = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ROOT / "deploy" / "docker" / "scripts" / "backup.ps1"),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        dump_path = Path(backup.stdout.strip().splitlines()[-1])
        assert dump_path.exists()
        assert dump_path.stat().st_size > 0
        assert not old_dump.exists()
        listed = _docker(
            "exec",
            "muzi-postgres",
            "pg_restore",
            "-l",
            _copy_in(dump_path),
        )
        assert "TABLE DATA" in listed.stdout or "users" in listed.stdout

        _drop_restore_db()
        _psql("postgres", f"CREATE DATABASE {RESTORE_DB}")
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ROOT / "deploy" / "docker" / "scripts" / "restore.ps1"),
                "-BackupFile",
                str(dump_path),
                "-TargetDatabase",
                RESTORE_DB,
                "-Confirm",
                "YES",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        restored = asyncio.run(_count_email(email, RESTORE_DB))
        assert restored == 1

        env = os.environ.copy()
        env["POSTGRES_DB"] = RESTORE_DB
        subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "base"],
            cwd=ROOT,
            check=True,
            env=env,
        )
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            check=True,
            env=env,
        )
        assert asyncio.run(_count_email(email, "muzi_llm")) == 1
        log = (BACKUP_DIR / "backup.log").read_text(encoding="utf-8")
        assert "OK file=" in log
        assert "RESTORE OK" in log
        loaded = Settings()
        secret_in_log = loaded.postgres_password in log or loaded.redis_password in log
        assert secret_in_log is False
    finally:
        _drop_restore_db()
        user_id = asyncio.run(_user_id(email))
        if user_id is not None:
            asyncio.run(_delete_user(user_id))


async def test_postgres_restart_keeps_rows(settings: Settings) -> None:
    del settings
    email = f"restart{uuid.uuid4().hex[:10]}@example.com"
    username = "r" + uuid.uuid4().hex[:10]
    user_id = await _insert_user(email, username)
    try:
        _docker("restart", "muzi-postgres")
        _wait_healthy("muzi-postgres")
        await dispose_engine()
        get_settings.cache_clear()
        init_engine(Settings())
        factory = get_session_factory()
        async with factory() as session:
            found = await session.scalar(
                text("SELECT email FROM users WHERE id = :id"),
                {"id": user_id},
            )
        assert found == email
    finally:
        await _delete_user(user_id)


def test_lifespan_reports_ready_and_releases_pools() -> None:
    get_settings.cache_clear()
    loaded = Settings()
    app = create_app(settings=loaded, provider=FakeProvider())
    with TestClient(app) as client:
        assert app.state.database_ready is True
        assert app.state.redis_ready is True
        assert client.get("/health").status_code == 200
    from app.core.database.redis import get_redis_or_none
    from app.core.database.session import get_engine_or_none

    assert get_engine_or_none() is None
    assert get_redis_or_none() is None


def _copy_in(path: Path) -> str:
    remote = "/tmp/muzi-list.dump"
    _docker("cp", str(path), f"muzi-postgres:{remote}")
    return remote


def _drop_restore_db() -> None:
    _psql(
        "postgres",
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = '{RESTORE_DB}' AND pid <> pg_backend_pid()",
    )
    _psql("postgres", f"DROP DATABASE IF EXISTS {RESTORE_DB}")


def _psql(database: str, sql: str) -> None:
    subprocess.run(
        [
            "docker",
            "exec",
            "muzi-postgres",
            "psql",
            "-U",
            "muzi_admin",
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


async def _insert_user(email: str, username: str) -> uuid.UUID:
    init_engine(Settings())
    try:
        factory = get_session_factory()
        async with factory() as session:
            user = User(email=email, username=username)
            session.add(user)
            await session.commit()
            return user.id
    finally:
        await dispose_engine()


async def _delete_user(user_id: uuid.UUID) -> None:
    init_engine(Settings())
    try:
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
            await session.commit()
    finally:
        await dispose_engine()


async def _user_id(email: str) -> uuid.UUID | None:
    init_engine(Settings())
    try:
        factory = get_session_factory()
        async with factory() as session:
            return await session.scalar(
                text("SELECT id FROM users WHERE email = :email"),
                {"email": email},
            )
    finally:
        await dispose_engine()


async def _count_email(email: str, database: str) -> int:
    settings = Settings().model_copy(update={"postgres_db": database})
    engine = create_async_engine(build_async_postgres_url(settings), poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            value = await connection.scalar(
                text("SELECT count(*) FROM users WHERE email = :email"),
                {"email": email},
            )
        return int(value or 0)
    finally:
        await engine.dispose()

