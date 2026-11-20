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

from app.tools.sandbox.outputs import OUTPUT_ENV, output_dir
from app.tools.sandbox.provider import (
    SandboxDependencyRejected,
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
            output = f"{completed['stdout']}{completed['stderr']}"
            return SandboxResult(
                exit_code=completed["exit_code"],
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

    def _dependency_plan(self, spec: SandboxSpec):
        """解析依赖策略；无依赖时返回 None。"""
        if not spec.dependencies:
            return None
        from app.tools.sandbox import dependencies as dep_policy

        try:
            return dep_policy.plan(
                spec.dependencies,
                self._settings,
                trusted=spec.dependencies_trusted,
            )
        except dep_policy.DependencyRejected as exc:
            raise SandboxDependencyRejected(exc.code, exc.message) from None

    def _command(self, spec: SandboxSpec, plan) -> list[str]:
        """有依赖时先装进 /app/.deps，再执行脚本。

        走预热缓存时 pip 完全不联网（``--no-index --find-links /wheels``），
        所以沙盒可以继续保持 ``--network none``。
        """
        if plan is None:
            return spec.argv
        pkgs = " ".join(shlex.quote(item) for item in plan.packages)
        script = " ".join(shlex.quote(part) for part in spec.argv)
        if plan.offline_from_cache:
            install = (
                "python -m pip install -q --no-input --disable-pip-version-check "
                "--only-binary=:all: --no-index --find-links /wheels "
                f"--target /app/.deps {pkgs}"
            )
            prefix = "export PYTHONPATH=/app/.deps"
        else:
            install = (
                "python -m pip install -q --no-input --disable-pip-version-check "
                f"--only-binary=:all: {pkgs}"
            )
            prefix = ":"
        return ["sh", "-c", f"{prefix} && {install} && exec {script}"]

    def _execute(self, host_dir: Path, spec: SandboxSpec) -> dict[str, object]:
        mem_mb = spec.memory_mb or self._mem_mb
        network = self._network if spec.network is None else spec.network
        plan = self._dependency_plan(spec)
        if plan is not None and not plan.offline_from_cache:
            # 只有回退到联网装包时才需要 bridge；
            # 预热缓存路径是纯本地解压，断网也能装。
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
        if plan is not None and plan.offline_from_cache and plan.cache_dir is not None:
            argv.extend(["-v", f"{plan.cache_dir}:/wheels:ro"])
        workspace = self._workspace_dir()
        if workspace is not None:
            argv.extend(["-v", f"{workspace}:/workspace:ro"])
        # 唯一的可写出口：产物目录读写挂载，容器里用 /output。工作区保持 ro。
        output = output_dir(self._settings)
        if output is not None:
            argv.extend(["-v", f"{output}:/output:rw"])
            argv.extend(["-e", f"{OUTPUT_ENV}=/output"])
        for key, value in (spec.env or {}).items():
            argv.extend(["-e", f"{key}={value}"])
        argv.append(self._image)
        argv.extend(self._command(spec, plan))
        out_path = host_dir / "_stdout.log"
        err_path = host_dir / "_stderr.log"
        try:
            with out_path.open("wb") as out, err_path.open("wb") as err:
                process = subprocess.Popen(
                    argv,
                    shell=False,
                    stdout=out,
                    stderr=err,
                    stdin=subprocess.DEVNULL,
                )
                try:
                    code = process.wait(timeout=spec.timeout + _DOCKER_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    subprocess.run(["docker", "rm", "-f", container], capture_output=True)
                    process.kill()
                    raise SandboxTimeoutError(_read_logs(out_path, err_path)) from None
        except OSError:
            raise SandboxExecutionError() from None
        return {
            "exit_code": code,
            "stdout": out_path.read_text(encoding="utf-8", errors="replace") if out_path.exists() else "",
            "stderr": err_path.read_text(encoding="utf-8", errors="replace") if err_path.exists() else "",
        }


def _read_logs(out_path: Path, err_path: Path) -> str:
    parts: list[str] = []
    for path in (out_path, err_path):
        try:
            if path.exists():
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "".join(parts)[-_MAX_OUTPUT:]
