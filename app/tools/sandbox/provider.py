"""沙盒执行的统一接口。

三个执行类工具（run_python / run_command / execute_skill_script）把「在隔离环境里
准备文件并执行命令」这一职责委托给 ``SandboxProvider``。当前有两种实现：

- ``SubprocessSandboxProvider``：临时目录 + 本地进程（默认，过渡隔离）
- ``AgentBoxSandboxProvider``：容器级隔离（Phase 1 引入）

Provider 只负责执行与输出收集，错误以异常形式抛出，由各 handler 翻译成自己的
错误码（code_timeout / command_timeout / SKILL_EXECUTION_TIMEOUT 等），从而保持
对外返回结构不变。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class SandboxSpec:
    """一次沙盒执行的完整描述。

    ``files`` 是需要写入沙盒工作目录的相对路径 → 字节内容（例如入口脚本、
    args.json、技能包资源）。``argv`` 是待执行的命令，其中 ``argv[0]`` 是
    **逻辑命令名**（``"python"`` 或白名单命令名），脚本/文件路径为相对沙盒
    cwd 的相对路径——各后端自行把逻辑命令解析成可执行路径（本地进程后端解析为
    ``sys.executable``/``shutil.which``，容器后端直接用镜像内 PATH）。
    """

    argv: list[str]
    files: dict[str, bytes] = field(default_factory=dict)
    timeout: float = 30.0
    sandbox_prefix: str = "muzi_"
    redact_script_name: str = ""
    memory_mb: int | None = None
    network: bool | None = None
    dependencies: tuple[str, ...] = ()
    # 技能清单声明的依赖：跳过白名单，但仍受硬禁令/体积/时长限制。
    dependencies_trusted: bool = False
    # 额外注入子进程的环境变量（工作区路径等）。后端会与最小环境合并。
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SandboxResult:
    exit_code: int
    output: str
    truncated: bool


class SandboxTimeoutError(Exception):
    """执行超时。

    ``partial_output`` 保留超时前已经产生的输出。整段脚本被硬杀时，这是模型
    唯一能用来判断「卡在哪一步」的证据，不能丢。
    """

    def __init__(self, partial_output: str = "") -> None:
        super().__init__("sandbox timeout")
        self.partial_output = partial_output


class SandboxExecutionError(Exception):
    """执行失败（无法启动进程或 OSError）。"""


class SandboxDependencyRejected(Exception):
    """依赖策略拒绝或安装失败；``code`` 直接作为对外错误码。

    两个后端共用同一个异常，``run_python`` 等 handler 才能用一套错误码回给模型。
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SandboxProvider(Protocol):
    def run(self, spec: SandboxSpec) -> SandboxResult:
        """按 spec 执行并返回结果；超时抛 ``SandboxTimeoutError``，启动失败抛
        ``SandboxExecutionError``。"""
        ...


def get_provider(settings) -> SandboxProvider:
    """按 settings.sandbox_backend 选择后端。settings 为 None 时回落本地进程后端。"""
    backend = getattr(settings, "sandbox_backend", "local")
    if backend == "agentbox":
        from app.tools.sandbox.agentbox_backend import AgentBoxSandboxProvider

        return AgentBoxSandboxProvider(settings)
    from app.tools.sandbox.subprocess_backend import SubprocessSandboxProvider

    return SubprocessSandboxProvider(settings)
