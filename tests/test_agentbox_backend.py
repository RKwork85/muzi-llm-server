"""AgentBox(docker) 后端的命令构造与超时清理（mock，不依赖 docker daemon）。

后端现在用 ``Popen`` + 日志文件执行，为的是超时被强杀时仍能取回已产生的输出。
所以这里用一个假进程把输出写进 Popen 收到的 stdout 句柄，再按需在 ``wait`` 抛超时。
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from app.tools.sandbox.agentbox_backend import AgentBoxSandboxProvider
from app.tools.sandbox.provider import SandboxSpec, SandboxTimeoutError
from tests.conftest import make_settings

_RUN_PATCH = "app.tools.sandbox.agentbox_backend.subprocess.run"
_POPEN_PATCH = "app.tools.sandbox.agentbox_backend.subprocess.Popen"


class _FakeProcess:
    """未使用；保留说明：假进程只需 ``wait`` / ``kill`` 两个方法。"""


def _popen(output: str = "", code: int = 0, timeout: bool = False):
    """构造一个可直接当 ``Popen`` 类使用的假实现。"""
    calls: list[list[str]] = []

    class Fake:
        def __init__(self, argv, **kwargs):
            calls.append(list(argv))
            self.argv = list(argv)
            self.returncode = code
            self.killed = False
            self._timeout = timeout
            handle = kwargs.get("stdout")
            if output and handle is not None and not handle.closed:
                handle.write(output.encode("utf-8"))
                handle.flush()

        def wait(self, timeout=None):
            if self._timeout:
                raise subprocess.TimeoutExpired(cmd="docker run", timeout=timeout)
            return self.returncode

        def kill(self) -> None:
            self.killed = True

    return Fake, calls


def test_agentbox_builds_isolated_docker_command() -> None:
    settings = make_settings(sandbox_backend="agentbox")
    provider = AgentBoxSandboxProvider(settings)
    spec = SandboxSpec(
        argv=["python", "-I", "-P", "-u", "main.py"],
        files={"main.py": b"print(1+1)\n"},
        timeout=10,
    )

    fake, calls = _popen(output="2\n")
    with patch(_RUN_PATCH) as run, patch(_POPEN_PATCH, fake):
        # docker image inspect：镜像已存在
        run.return_value = SimpleNamespace(returncode=0)
        result = provider.run(spec)

    docker_argv = calls[0]
    assert docker_argv[0] == "docker"
    assert docker_argv[1] == "run"
    assert "--rm" in docker_argv
    # 联网默认开启：装依赖必须联网；只有显式关掉才用 --network none。
    assert docker_argv[docker_argv.index("--network") + 1] == "bridge"
    assert docker_argv[docker_argv.index("--memory") + 1] == "512m"
    assert docker_argv[docker_argv.index("--cpus") + 1] == "1.0"
    assert "--pull" in docker_argv and docker_argv[docker_argv.index("--pull") + 1] == "never"
    mount = docker_argv[docker_argv.index("-v") + 1]
    assert mount.endswith(":/app")
    assert docker_argv[docker_argv.index("-w") + 1] == "/app"
    assert "python:3.12-slim" in docker_argv
    # 逻辑命令原样传入容器
    assert docker_argv[-5:] == ["python", "-I", "-P", "-u", "main.py"]

    assert result.exit_code == 0
    assert result.output == "2\n"


def test_agentbox_disables_network_only_when_asked() -> None:
    settings = make_settings(sandbox_backend="agentbox", sandbox_network_enabled=False)
    provider = AgentBoxSandboxProvider(settings)
    spec = SandboxSpec(argv=["python", "-c", "print(1)"], timeout=10)

    fake, calls = _popen(output="1\n")
    with patch(_RUN_PATCH) as run, patch(_POPEN_PATCH, fake):
        run.return_value = SimpleNamespace(returncode=0)
        provider.run(spec)

    docker_argv = calls[0]
    assert docker_argv[docker_argv.index("--network") + 1] == "none"


def test_agentbox_timeout_removes_container_and_keeps_partial_output() -> None:
    settings = make_settings(sandbox_backend="agentbox")
    provider = AgentBoxSandboxProvider(settings)
    spec = SandboxSpec(argv=["python", "-c", "import time; time.sleep(999)"])

    fake, _ = _popen(output="step 1 ok\n", timeout=True)
    with patch(_RUN_PATCH) as run, patch(_POPEN_PATCH, fake):
        run.return_value = SimpleNamespace(returncode=0)  # image inspect / docker rm
        try:
            provider.run(spec)
        except SandboxTimeoutError as exc:
            partial = exc.partial_output
        else:
            raise AssertionError("expected SandboxTimeoutError")

    cleanup = run.call_args_list[-1].args[0]
    assert cleanup[0] == "docker"
    assert cleanup[1] == "rm"
    assert cleanup[2] == "-f"
    # 超时前已经打印的内容必须留给模型，否则它只能猜自己卡在哪一步。
    assert "step 1 ok" in partial


def test_agentbox_mounts_workspace_readonly(tmp_path) -> None:
    from app.tools.workspace_scope import CURRENT_WORKSPACE_SCOPE

    scope = "b" * 32
    settings = make_settings(sandbox_backend="agentbox", tools_workspace_root=str(tmp_path))
    provider = AgentBoxSandboxProvider(settings)
    spec = SandboxSpec(argv=["python", "-c", "print(1)"])

    fake, calls = _popen(output="1\n")
    with patch(_RUN_PATCH) as run, patch(_POPEN_PATCH, fake):
        run.return_value = SimpleNamespace(returncode=0)
        token = CURRENT_WORKSPACE_SCOPE.set(scope)
        try:
            provider.run(spec)
        finally:
            CURRENT_WORKSPACE_SCOPE.reset(token)

    docker_argv = calls[0]
    workspace_mounts = [item for item in docker_argv if item.endswith(":/workspace:ro")]
    assert len(workspace_mounts) == 1
    assert scope in workspace_mounts[0]
