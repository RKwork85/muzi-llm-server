"""AgentBox 后端：用本地 Docker CLI 创建 per-run 短命容器执行代码。

每个 run 创建一个一次性容器（--rm --name），把 ``spec.files`` 写入 host 临时目录
并挂载为容器工作目录 ``/app``，执行逻辑命令 ``argv``，收集 stdout/stderr 后销毁。
网络默认关闭（``--network none``）、内存/swap/CPU/进程数受限。

这是规划里 AgentBoxBackend 的自托管 Docker 实现（用户已选定 docker_self 方案），
不依赖第三方 AgentBox 服务；容器运行结束即销毁（per-run short-lived）。
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from app.tools.sandbox.provider import (
    SandboxExecutionError,
    SandboxResult,
    SandboxSpec,
    SandboxTimeoutError,
)

_MAX_OUTPUT = 8000
# pull 镜像 + 启动/销毁容器是相对固定的开销，脚本本身的超时由 spec.timeout 承担
_DOCKER_GRACE_SECONDS = 60.0


class AgentBoxSandboxProvider:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._image = getattr(settings, "sandbox_docker_image", "python:3.12-slim")
        self._mem_mb = getattr(settings, "sandbox_mem_mb", 512)
        self._network = getattr(settings, "sandbox_network_enabled", False)
        self._image_ready = False

    def run(self, spec: SandboxSpec) -> SandboxResult:
        self._ensure_image()
        host_dir = Path(tempfile.mkdtemp(prefix="muzi_box_"))
        try:
            self._write_files(host_dir, spec.files)
            completed = self._execute(host_dir, spec)
            output = f"{completed.stdout}{completed.stderr}"
            return SandboxResult(
                exit_code=completed.returncode,
                output=output[:_MAX_OUTPUT],
                truncated=len(output) > _MAX_OUTPUT,
            )
        finally:
            shutil.rmtree(host_dir, ignore_errors=True)

    def _ensure_image(self) -> None:
        if self._image_ready:
            return
        inspect = subprocess.run(["docker", "image", "inspect", self._image], capture_output=True)
        if inspect.returncode != 0:
            subprocess.run(["docker", "pull", self._image], check=False)
        self._image_ready = True

    def _write_files(self, host_dir: Path, files: dict[str, bytes]) -> None:
        for relative, data in files.items():
            target = host_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    def _workspace_dir(self) -> Path | None:
        """当前用户的 workspace scope 目录（只读挂载源）。

        依赖 CURRENT_WORKSPACE_SCOPE 上下文（agent 工具执行时由 runtime 传播）。
        无 workspace 配置、无合法 scope 时返回 None，不挂载。
        """
        from app.tools.file_sandbox import FileDenied
        from app.tools.file_tools import workspace_root

        try:
            return workspace_root(self._settings)
        except FileDenied:
            return None

    def _command(self, spec: SandboxSpec) -> list[str]:
        if not spec.dependencies:
            return spec.argv
        pkgs = " ".join(spec.dependencies)
        script = " ".join(shlex.quote(part) for part in spec.argv)
        return [
            "sh", "-c",
            f"python -m pip install -q --disable-pip-version-check {pkgs} && exec {script}",
        ]

    def _execute(self, host_dir: Path, spec: SandboxSpec) -> subprocess.CompletedProcess[str]:
        mem_mb = spec.memory_mb or self._mem_mb
        network = self._network if spec.network is None else spec.network
        if spec.dependencies:
            # pip install 需要访问 PyPI
            network = True
        container = f"muzi_box_{uuid.uuid4().hex[:12]}"
        argv = [
            "docker", "run", "--rm", "--name", container,
            "--pull", "never",
            "--network", "none" if not network else "bridge",
            "--memory", f"{mem_mb}m",
            "--memory-swap", f"{mem_mb}m",
            "--cpus", "1.0",
            "--pids-limit", "64",
            "-v", f"{host_dir}:/app",
            "-w", "/app",
        ]
        workspace = self._workspace_dir()
        if workspace is not None:
            argv.extend(["-v", f"{workspace}:/workspace:ro"])
        argv.append(self._image)
        argv.extend(self._command(spec))
        try:
            return subprocess.run(
                argv,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=spec.timeout + _DOCKER_GRACE_SECONDS,
                stdin=subprocess.DEVNULL,
                check=False,
            )
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "rm", "-f", container], capture_output=True)
            raise SandboxTimeoutError() from None
        except OSError:
            raise SandboxExecutionError() from None
