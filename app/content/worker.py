"""内容创作 worker 入口（Taskiq）。

第二阶段把上一轮的「standalone 轮询 PostgreSQL」切换为 Taskiq：投递由 API 层在
事务提交后触发（``.kiq()``），消费由本进程的 Taskiq 接收器完成。

启动：

    cd muzi-llm-server
    python -m app.content.worker

等价于 ``taskiq worker app.content.tasks:broker --workers 1``。
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
    args = WorkerArgs(
        broker="app.content.tasks:broker",
        modules=["app.content.tasks"],
        workers=1,
    )
    run_worker(args)


if __name__ == "__main__":
    main()
