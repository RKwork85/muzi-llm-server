"""FastAPI 数据库依赖。成功时提交，异常时回滚。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database.redis import get_redis
from app.core.database.session import get_session_factory
from app.core.exceptions import DatabaseUnavailableError


async def get_db_session() -> AsyncIterator[AsyncSession]:
    try:
        factory = get_session_factory()
    except RuntimeError:
        raise DatabaseUnavailableError() from None
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db_session_optional() -> AsyncIterator[AsyncSession | None]:
    """数据库不可用时不报错，返回 None，供可选增强类查询使用。"""
    try:
        factory = get_session_factory()
    except RuntimeError:
        yield None
        return
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_redis_client() -> Redis:
    return get_redis()


DbSession = Annotated[AsyncSession, Depends(get_db_session)]
DbSessionOptional = Annotated[AsyncSession | None, Depends(get_db_session_optional)]
RedisClient = Annotated[Redis, Depends(get_redis_client)]
