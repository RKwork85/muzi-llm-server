"""部署文件与示例配置不包含真实密码。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_compose_binds_localhost_and_postgres18_volume() -> None:
    text = (ROOT / "deploy" / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "image: postgres:18" in text
    assert "image: redis:7" in text
    assert "container_name: muzi-postgres" in text
    assert "container_name: muzi-redis" in text
    assert "127.0.0.1:${POSTGRES_HOST_PORT}:5432" in text
    assert "127.0.0.1:${REDIS_HOST_PORT}:6379" in text
    assert "muzi-postgres-data:/var/lib/postgresql" in text
    assert "/var/lib/postgresql/data" not in text
    assert "muzi-redis-data:/data" in text
    assert "name: muzi-backend" in text
    assert "pg_isready" in text
    assert "redis-cli" in text
    assert "requirepass" not in text
    assert "replace-with-generated-password" not in text


def test_examples_and_readme_have_no_generated_passwords() -> None:
    example = (ROOT / "deploy" / "docker" / ".env.example").read_text(encoding="utf-8")
    readme = (ROOT / "deploy" / "docker" / "README.md").read_text(encoding="utf-8")
    app_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD=replace-with-generated-password" in example
    assert "REDIS_PASSWORD=replace-with-generated-password" in example
    assert "POSTGRES_PASSWORD=" in app_example.splitlines()
    placeholder = "replace-with-generated-password"
    assert re.search(r"POSTGRES_PASSWORD=[A-Za-z0-9_-]{16,}", example.replace(placeholder, "")) is None
    assert re.search(r"REDIS_PASSWORD=[A-Za-z0-9_-]{16,}", readme) is None
    assert re.search(r"sk-[A-Za-z0-9]{16,}", readme) is None


def test_gitignore_skips_dumps_and_env() -> None:
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in text.splitlines()
    assert "deploy/docker/backups/*" in text
    assert "!deploy/docker/backups/.gitkeep" in text


def test_requirements_include_database_packages() -> None:
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    for name in ("sqlalchemy[asyncio]", "asyncpg", "alembic", "redis", "argon2-cffi"):
        assert name in text


def test_entrypoint_uses_lf_and_aof() -> None:
    raw = (ROOT / "deploy" / "docker" / "redis" / "entrypoint.sh").read_bytes()
    assert b"\r\n" not in raw
    text = raw.decode("utf-8")
    assert "appendonly yes" in text
    assert "appendfsync everysec" in text
    assert "save 900 1" in text
    assert "requirepass ${REDIS_PASSWORD}" in text
