"""内容创作任务队列的 Taskiq broker。

设计文档定死选型：Taskiq + Redis（复用现有 Redis，零新增中间件）。本模块只负责
定义 broker 实例；任务函数在 ``app/content/tasks.py`` 里用 ``@broker.task`` 注册。

- 主服务（uvicorn）进程 import 本模块只为拿到任务函数做 ``.kiq()`` 投递，
  broker 惰性连接，import 无副作用。
- worker 进程（``python -m app.content.worker``）消费 broker 队列。

broker 用独立 Redis db（``taskiq_redis_db``），键自带 ``taskiq:`` 前缀，与业务键
隔离：db0 主服务业务、db1 muzi-comfy-service、db2 本队列。
"""

from __future__ import annotations

from urllib.parse import quote

from taskiq_redis import ListQueueBroker

from app.core.config import load_settings


def _broker_url() -> str:
    """构造 redis-py 兼容的 broker URL（密码做 URL 编码，避免特殊字符破坏解析）。"""
    settings = load_settings()
    password = quote(settings.redis_password, safe="") if settings.redis_password else ""
    auth = f":{password}@" if password else ""
    return (
        f"redis://{auth}{settings.redis_host}:{settings.redis_port}"
        f"/{settings.taskiq_redis_db}"
    )


# FIFO 队列 broker：每个任务只被一个 worker 消费（区别于 PubSubBroker 的广播语义）。
# 注意：redis-py 8.x 默认 socket_timeout=5s，会让 ListQueueBroker 的阻塞 brpop 每 5 秒
# 超时一次；显式置 None 恢复无限阻塞等待，由 brpop 自身决定阻塞时长。
broker = ListQueueBroker(url=_broker_url(), socket_timeout=None)
