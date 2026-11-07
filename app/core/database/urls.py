"""数据库 URL。只在连接层使用，禁止写入日志。"""

from __future__ import annotations

from urllib.parse import quote_plus

from app.core.config import Settings


def build_async_postgres_url(settings: Settings) -> str:
    user = quote_plus(settings.postgres_user)
    password = quote_plus(settings.postgres_password)
    database = quote_plus(settings.postgres_db)
    host = settings.postgres_host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return (
        f"postgresql+asyncpg://{user}:{password}@{host}:{settings.postgres_port}/{database}"
    )
