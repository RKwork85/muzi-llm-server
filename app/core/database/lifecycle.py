"""应用启动和关闭时的数据库连接。失败只记录异常类型，不记录连接串。"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text

from app.core.config import Settings
from app.core.database.redis import close_redis, get_redis, init_redis
from app.core.database.session import dispose_engine, get_session_factory, init_engine

logger = logging.getLogger("muzi_llm")


async def startup_data_backends(settings: Settings) -> bool:
    if not settings.database_startup_required:
        logger.info("数据库启动检查已关闭")
        return False
    try:
        init_engine(settings)
        init_redis(settings)
        await _wait_postgres(settings)
        await _wait_redis(settings)
    except Exception:
        logger.error("数据后端启动失败")
        await close_redis()
        await dispose_engine()
        raise
    logger.info(
        "数据后端已就绪 postgres_host=%s postgres_db=%s redis_host=%s redis_db=%s",
        settings.postgres_host,
        settings.postgres_db,
        settings.redis_host,
        settings.redis_db,
    )
    return True


async def shutdown_data_backends(*, enabled: bool) -> None:
    if not enabled:
        return
    await close_redis()
    await dispose_engine()


async def _wait_postgres(settings: Settings) -> None:
    factory = get_session_factory()
    delay = settings.db_startup_retry_seconds
    last_type = "Exception"
    for attempt in range(1, settings.db_startup_attempts + 1):
        try:
            async with factory() as session:
                await session.execute(text("SELECT 1"))
            return
        except Exception as exc:
            last_type = type(exc).__name__
            logger.warning("PostgreSQL 尚未就绪 attempt=%s error_type=%s", attempt, last_type)
            if attempt == settings.db_startup_attempts:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, 8)
    raise RuntimeError(f"PostgreSQL 不可用: {last_type}") from None


async def _wait_redis(settings: Settings) -> None:
    client = get_redis()
    delay = settings.db_startup_retry_seconds
    last_type = "Exception"
    for attempt in range(1, settings.db_startup_attempts + 1):
        try:
            pong = await client.ping()
            if pong is not True:
                raise RuntimeError("Redis ping 未返回 PONG")
            return
        except Exception as exc:
            last_type = type(exc).__name__
            logger.warning("Redis 尚未就绪 attempt=%s error_type=%s", attempt, last_type)
            if attempt == settings.db_startup_attempts:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, 8)
    raise RuntimeError(f"Redis 不可用: {last_type}") from None
