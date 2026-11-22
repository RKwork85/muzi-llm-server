"""进程内 Taskiq 消费器。

把内容任务队列的消费直接跑在 uvicorn 的事件循环里，随 FastAPI lifespan 启动/停止，
无需再单独拉起 ``python -m app.content.worker`` 进程——启动后端即启动任务消费，
服务生命周期统一管理，不再有「游离进程重启后丢失」的问题。

生产要水平扩展（多进程 / 多机并发消费）时，可关闭 ``CONTENT_WORKER_INPROCESS``
改回独立 worker 进程（见 worker.py）。两者共用同一个 broker 实例与任务注册，
互不冲突。
"""

from __future__ import annotations

import asyncio
import logging

from taskiq.receiver import Receiver

from app.content import tasks  # noqa: F401  # 确保任务函数注册进 broker
from app.content.broker import broker

logger = logging.getLogger("muzi_llm")


class InProcessWorker:
    """在服务进程内消费 Taskiq 队列的 worker。"""

    def __init__(self, max_async_tasks: int = 1) -> None:
        # ListQueueBroker 是 FIFO 队列，每个任务只被一个消费者处理；进程内串行
        # （max_async_tasks=1）对重采集任务最稳，避免同时开多个 Playwright 浏览器。
        self._receiver = Receiver(broker, max_async_tasks=max_async_tasks)
        self._finish_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._finish_event = asyncio.Event()
        self._task = asyncio.create_task(
            self._receiver.listen(self._finish_event),
            name="content-taskiq-receiver",
        )
        self._task.add_done_callback(self._on_done)
        logger.info("content task worker 已在服务进程内启动")

    def _on_done(self, task: asyncio.Task[None]) -> None:
        # 提前消费异常，避免 asyncio 报「Task exception was never retrieved」，
        # 同时把消费循环的崩溃记到日志，便于排查。
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("content task worker 异常退出", exc_info=exc)

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        # 置 finish_event 让 receiver 停止拉取新任务，跑完手头在途任务后优雅退出。
        self._finish_event.set()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
        await broker.shutdown()
        logger.info("content task worker 已停止")
