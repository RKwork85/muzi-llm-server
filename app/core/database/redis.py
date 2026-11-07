"""进程级 Redis 客户端。"""

from __future__ import annotations

from redis.asyncio import Redis

from app.core.config import Settings

_client: Redis | None = None


def init_redis(settings: Settings) -> Redis:
    global _client
    if _client is not None:
        return _client
    _client = Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        password=settings.redis_password or None,
        decode_responses=True,
        socket_connect_timeout=settings.redis_socket_timeout_seconds,
        socket_timeout=settings.redis_socket_timeout_seconds,
        health_check_interval=30,
        max_connections=settings.redis_max_connections,
        socket_keepalive=True,
    )
    return _client


def get_redis() -> Redis:
    if _client is None:
        raise RuntimeError("Redis 未初始化")
    return _client


def get_redis_or_none() -> Redis | None:
    return _client


async def close_redis() -> None:
    global _client
    client = _client
    _client = None
    if client is not None:
        await client.aclose()
