"""AgentBox(docker) 后端的命令构造与超时清理（mock，不依赖 docker daemon）。"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from app.tools.sandbox.agentbox_backend import AgentBoxSandboxProvider
from app.tools.sandbox.provider import SandboxSpec, SandboxTimeoutError
from tests.conftest import make_settings


def test_agentbox_builds_isolated_docker_command() -> None:
    from unittest.mock import patch

    settings = make_settings(sandbox_backend="agentbox")
    provider = AgentBoxSandboxProvider(settings)
    spec = SandboxSpec(
        argv=["python", "-I", "-P", "-u", "main.py"],
        files={"main.py": b"print(1+1)\n"},
        timeout=10,
    )

    with patch("app.tools.sandbox.agentbox_backend.subprocess.run") as run:
        # 第 1 次：docker image inspect（镜像已存在）；第 2 次：docker run
        run.side_effect = [
            SimpleNamespace(returncode=0),
            SimpleNamespace(returncode=0, stdout="2\n", stderr=""),
        ]
        result = provider.run(spec)

    docker_argv = run.call_args_list[1].args[0]
    assert docker_argv[0] == "docker"
    assert docker_argv[1] == "run"
    assert "--rm" in docker_argv
    assert docker_argv[docker_argv.index("--network") + 1] == "none"
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


def test_agentbox_timeout_removes_container() -> None:
    from unittest.mock import patch

    settings = make_settings(sandbox_backend="agentbox")
    provider = AgentBoxSandboxProvider(settings)
    spec = SandboxSpec(argv=["python", "-c", "import time; time.sleep(999)"])

    with patch("app.tools.sandbox.agentbox_backend.subprocess.run") as run:
        run.side_effect = [
            SimpleNamespace(returncode=0),  # image inspect
            subprocess.TimeoutExpired(cmd="docker run", timeout=1),  # docker run 超时
            SimpleNamespace(returncode=0),  # docker rm -f
        ]
        try:
            provider.run(spec)
        except SandboxTimeoutError:
            pass
        else:
            raise AssertionError("expected SandboxTimeoutError")

    cleanup = run.call_args_list[2].args[0]
    assert cleanup[0] == "docker"
    assert cleanup[1] == "rm"
    assert cleanup[2] == "-f"


def test_agentbox_mounts_workspace_readonly(tmp_path) -> None:
    from unittest.mock import patch

    from app.tools.workspace_scope import CURRENT_WORKSPACE_SCOPE

    scope = "b" * 32
    settings = make_settings(sandbox_backend="agentbox", tools_workspace_root=str(tmp_path))
    provider = AgentBoxSandboxProvider(settings)
    spec = SandboxSpec(argv=["python", "-c", "print(1)"])

    with patch("app.tools.sandbox.agentbox_backend.subprocess.run") as run:
        run.side_effect = [
            SimpleNamespace(returncode=0),  # image inspect
            SimpleNamespace(returncode=0, stdout="", stderr=""),  # docker run
        ]
        scope_token = CURRENT_WORKSPACE_SCOPE.set(scope)
        try:
            provider.run(spec)
        finally:
            CURRENT_WORKSPACE_SCOPE.reset(scope_token)

    docker_argv = run.call_args_list[1].args[0]
    workspace_mounts = [a for a in docker_argv if a.endswith(":/workspace:ro")]
    assert len(workspace_mounts) == 1
    assert scope in workspace_mounts[0]
