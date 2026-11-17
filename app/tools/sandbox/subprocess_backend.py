"""临时目录 + subprocess 的沙盒实现。

这是 Docker 容器池之前的过渡隔离（默认后端）：每次执行在一个新建的临时目录内
进行（cwd 锁死），结束后目录「阅后即焚」删除，不残留脚本、字节码或中间文件。
相对路径读写只落在临时目录，绝对路径逃逸由后续容器方案兜底。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from app.tools.sandbox_dir import make_sandbox, purge, redact_paths, safe_env
from app.tools.sandbox.provider import (
    SandboxExecutionError,
    SandboxResult,
    SandboxSpec,
    SandboxTimeoutError,
)

_MAX_OUTPUT = 8000


class SubprocessSandboxProvider:
    """无状态后端；每次 run 都创建新临时目录并阅后即焚。"""

    def run(self, spec: SandboxSpec) -> SandboxResult:
        sandbox = make_sandbox(spec.sandbox_prefix)
        try:
            self._write_files(sandbox, spec.files)
            completed = self._execute(sandbox, spec)
            output = redact_paths(
                f"{completed.stdout}{completed.stderr}",
                sandbox,
                script_name=spec.redact_script_name,
            )
            return SandboxResult(
                exit_code=completed.returncode,
                output=output[:_MAX_OUTPUT],
                truncated=len(output) > _MAX_OUTPUT,
            )
        finally:
            purge(sandbox)

    def _write_files(self, sandbox: Path, files: dict[str, bytes]) -> None:
        for relative, data in files.items():
            target = sandbox / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    def _resolve(self, argv: list[str]) -> list[str]:
        if argv[0] == "python":
            return [sys.executable, *argv[1:]]
        executable = shutil.which(argv[0])
        if not executable:
            raise SandboxExecutionError()
        return [executable, *argv[1:]]

    def _execute(self, sandbox: Path, spec: SandboxSpec) -> subprocess.CompletedProcess[str]:
        resolved = self._resolve(spec.argv)
        try:
            return subprocess.run(
                resolved,
                cwd=str(sandbox),
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=spec.timeout,
                env=safe_env(),
                stdin=subprocess.DEVNULL,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise SandboxTimeoutError() from None
        except OSError:
            raise SandboxExecutionError() from None
