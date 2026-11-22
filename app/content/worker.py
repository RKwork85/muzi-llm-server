"""内容创作 worker 入口（Taskiq）。

第二阶段把上一轮的「standalone 轮询 PostgreSQL」切换为 Taskiq：投递由 API 层在
事务提交后触发（``.kiq()``），消费由本进程的 Taskiq 接收器完成。

启动：

    cd muzi-llm-server
    python -m app.content.worker

等价于 ``taskiq worker app.content.tasks:broker --workers 1``。

⚠️ 默认情况下**不需要**再启动本进程：``CONTENT_WORKER_INPROCESS=true``（默认）
时，API 进程（uvicorn lifespan）已经内置了任务消费者。此时再起独立 worker 会让
同一个 Redis 队列出现两个消费者，任务被随机分给其中一个；旧进程若加载的是历史
代码，就会表现成「采集时好时坏 / 随机报 采集服务未返回 preview_data」。
因此这里在检测到进程内消费开启时直接拒绝启动，从源头堵住双消费者。
"""

from __future__ import annotations

import logging

from taskiq.cli.worker.args import WorkerArgs
from taskiq.cli.worker.run import run_worker


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # 延迟导入：避免在只需要解析 worker 参数的场景下初始化整套配置/DB。
    from app.core.config import load_settings

    settings = load_settings()
    if settings.content_worker_inprocess:
        raise SystemExit(
            "CONTENT_WORKER_INPROCESS=true：内容任务已由 API 进程（uvicorn lifespan）"
            "内置消费，独立 worker 会造成「双消费者」——同一队列被两个进程争抢，"
            "任务可能落到旧代码进程上，表现为采集随机失败。\n"
            "如确需独立 worker 水平扩展，请先把 CONTENT_WORKER_INPROCESS 设为 false，"
            "再执行 python -m app.content.worker。"
        )
    args = WorkerArgs(
        broker="app.content.tasks:broker",
        modules=["app.content.tasks"],
        workers=1,
    )
    run_worker(args)


if __name__ == "__main__":
    main()
