"""沙盒执行后端包。

三个执行类工具（run_python / run_command / execute_skill_script）通过
``SandboxProvider`` 接口把「隔离执行」委托给具体后端：

- ``SubprocessSandboxProvider``：临时目录 + 本地进程（默认，过渡隔离）
- ``AgentBoxSandboxProvider``：容器级隔离（Phase 1 引入）
"""

from app.tools.sandbox.provider import (
    SandboxExecutionError,
    SandboxResult,
    SandboxSpec,
    SandboxTimeoutError,
    get_provider,
)

__all__ = [
    "SandboxExecutionError",
    "SandboxResult",
    "SandboxSpec",
    "SandboxTimeoutError",
    "get_provider",
]
