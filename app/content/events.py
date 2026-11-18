"""内容创作任务的实时事件广播（SSE）。

链路：任务状态机每次流转 / 进度更新 → 本模块发布事件 → 已登录浏览器通过
SSE（``GET /api/v1/content/tasks/{task_id}/events``）实时收到，无需轮询。

两条通道互补（与 ``app/services/recharge_events.py`` 同构）：

- **进程内**：``asyncio.Queue`` 扇出。Redis 不可用时也能工作，覆盖单进程部署。
- **跨进程**：Redis pub/sub。worker 是独立进程，写库后靠这条把事件送回主服务进程，
  再由主服务进程推给浏览器。

事件信封带 ``process`` 字段（本进程随机 id）。发布端已经就地扇出过一次，桥接收到
自己发出的回声时按该字段丢弃，避免同一条事件推送两次。

> 事件在 ``session.flush()`` 之后、``commit()`` 之前发布，存在毫秒级窗口：
> 订阅方极快地去读库可能读到旧值。内容进度非资金操作，前端同时有轮询兜底，
> 该窗口可接受。若要严格一致，请在 commit 后再发布。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.core.database.redis import get_redis_or_none

logger = logging.getLogger(__name__)

CHANNEL_PREFIX = "muzi:content:events:"

# 心跳间隔：连接空闲时也要有字节流动，避免代理与浏览器判定连接已死。
HEARTBEAT_SECONDS = 15.0

# 单个订阅者最多积压的事件数。消费端卡住时丢弃新事件，绝不阻塞任务主链路。
_QUEUE_MAXSIZE = 64

_PROCESS_ID = uuid.uuid4().hex

_subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
_bridge_task: asyncio.Task[None] | None = None
# 进程关闭标记：置位后所有订阅者尽快结束，不再发心跳，让优雅关闭能真正完成。
_shutdown = asyncio.Event()


def _channel(user_id: str) -> str:
    return f"{CHANNEL_PREFIX}{user_id}"


def _fanout(user_id: str, event: dict[str, Any]) -> None:
    """把事件投递给本进程内该用户的所有订阅者。"""
    queues = _subscribers.get(user_id)
    if not queues:
        return
    for queue in tuple(queues):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(
                "内容事件队列已满，丢弃事件 user=%s type=%s", user_id, event.get("type")
            )


async def publish(user_id: object, event: dict[str, Any]) -> None:
    """发布一条内容任务事件。

    只应在数据库变更**已写入（flush 之后）**调用。本函数不抛异常：实时推送属于
    增强能力，失败不能影响已成功的状态流转。
    """
    key = str(user_id)

    # 本进程立即送达，不依赖 Redis 往返。
    _fanout(key, event)

    redis = get_redis_or_none()
    if redis is None:
        return
    try:
        await redis.publish(
            _channel(key),
            json.dumps(
                {"process": _PROCESS_ID, "user_id": key, "event": event},
                ensure_ascii=False,
            ),
        )
    except Exception:
        logger.warning("内容事件发布失败 user=%s", key, exc_info=True)


async def _bridge_loop() -> None:
    """订阅 Redis 上其他进程发布的事件，转给本进程的订阅者。"""
    redis = get_redis_or_none()
    if redis is None:
        return
    pubsub = redis.pubsub()
    try:
        await pubsub.psubscribe(f"{CHANNEL_PREFIX}*")
        async for message in pubsub.listen():
            if message.get("type") != "pmessage":
                continue
            try:
                data = json.loads(message.get("data") or "{}")
            except (TypeError, ValueError):
                continue
            if data.get("process") == _PROCESS_ID:
                continue  # 本进程发出的事件：已在 publish 时就地送达
            user_id = data.get("user_id")
            event = data.get("event")
            if isinstance(user_id, str) and isinstance(event, dict):
                _fanout(user_id, event)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("内容事件 Redis 桥接异常退出", exc_info=True)
    finally:
        try:
            await pubsub.punsubscribe()
            await pubsub.aclose()
        except Exception:
            pass


def _ensure_bridge() -> None:
    """首个订阅者到来时惰性启动桥接任务（Redis 可用才启动）。"""
    global _bridge_task
    if _bridge_task is not None and not _bridge_task.done():
        return
    if get_redis_or_none() is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _bridge_task = loop.create_task(_bridge_loop())


async def subscribe(user_id: object) -> AsyncIterator[dict[str, Any] | None]:
    """订阅某用户的内容任务事件。

    yield 事件字典；yield ``None`` 表示心跳超时，调用方应发送保活帧维持连接。
    生成器被关闭（客户端断开）时自动注销，不会泄漏队列。
    进程开始关闭（``close_all``）时立即结束，避免长连接拖住 uvicorn 的优雅关闭。
    """
    key = str(user_id)
    _ensure_bridge()

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
    _subscribers.setdefault(key, set()).add(queue)
    try:
        while not _shutdown.is_set():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield None
                continue
            yield event
    finally:
        queues = _subscribers.get(key)
        if queues is not None:
            queues.discard(queue)
            if not queues:
                _subscribers.pop(key, None)


def close_all() -> None:
    """关闭所有内容事件流（服务停机 / 热重载前调用）。"""
    _shutdown.set()
    for queues in list(_subscribers.values()):
        for queue in list(queues):
            try:
                queue.put_nowait(None)  # 唤醒阻塞在 get() 上的订阅者
            except asyncio.QueueFull:
                pass


def reset() -> None:
    """恢复为可订阅状态（进程内重启 lifespan 时用，正常部署只需 ``close_all``）。"""
    _shutdown.clear()
