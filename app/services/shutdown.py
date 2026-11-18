"""进程关闭钩子：在 uvicorn「等待连接关闭」之前先打断长连接。

uvicorn 的优雅关闭顺序是：

    停止接受新连接 → 等待在途连接自然结束 → 才执行 lifespan shutdown

SSE 长连接永远不会自然结束，于是进程会永久停在
`Waiting for connections to close`：端口还在，但一个请求都不再处理。
前端那边没有超时的 fetch 就一直 pending，用户看到的就是整页卡死。

因为 lifespan 里的清理排在上面第二步之后，放在那里已经来不及了，
所以这里接管进程信号：收到 SIGINT/SIGTERM 时先关掉实时事件流，
再把控制权交还给 uvicorn 原本的信号处理器。
"""

from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Callable

from app.services.recharge_events import close_all as close_recharge_events

logger = logging.getLogger(__name__)

_installed = False
_previous: dict[int, Callable | int | None] = {}


def _terminate_streams() -> None:
    try:
        close_recharge_events()
    except Exception:  # 清理失败绝不能挡着进程退出
        logger.warning("关闭实时事件流失败", exc_info=True)


def _handle(signum: int, frame: object) -> None:
    _terminate_streams()
    previous = _previous.get(signum)
    if callable(previous):
        # 交还给 uvicorn 的 handle_exit：它会把 should_exit 置位，进入正常关闭流程。
        previous(signum, frame)
        return
    # 没有可委托的处理器（独立脚本 / 测试场景）：恢复默认语义再退出。
    try:
        signal.signal(signum, signal.SIG_DFL)
    except (ValueError, OSError):
        pass
    if signum == signal.SIGINT:
        raise KeyboardInterrupt
    raise SystemExit(0)


def install() -> None:
    """安装信号钩子（幂等）。只能在主线程调用，否则信号无法注册。"""
    global _installed
    if _installed:
        return
    if threading.current_thread() is not threading.main_thread():
        return
    installed_any = False
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            _previous[sig] = signal.getsignal(sig)
            signal.signal(sig, _handle)
        except (ValueError, OSError):
            continue
        installed_any = True
    _installed = installed_any
