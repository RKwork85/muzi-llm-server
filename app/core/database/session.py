"""进程级异步引擎。请求路径只借用 Session，不新建 Engine。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.core.database.urls import build_async_postgres_url

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(settings: Settings) -> AsyncEngine:
    global _engine, _session_factory
    if _engine is not None:
        return _engine
    engine = create_async_engine(
        build_async_postgres_url(settings),
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_recycle=settings.db_pool_recycle_seconds,
        echo=False,
        hide_parameters=True,
        connect_args={
            "timeout": settings.db_connect_timeout_seconds,
            "command_timeout": settings.db_command_timeout_seconds,
            "server_settings": {"application_name": "muzi-llm"},
        },
    )
    _engine = engine
    _session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("PostgreSQL 未初始化")
    return _engine


def get_engine_or_none() -> AsyncEngine | None:
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("PostgreSQL 未初始化")
    return _session_factory


async def dispose_engine() -> None:
    global _engine, _session_factory
    engine = _engine
    _engine = None
    _session_factory = None
    if engine is not None:
        await engine.dispose()
