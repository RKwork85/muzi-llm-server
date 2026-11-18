"""临时目录 + subprocess 的沙盒实现。

这是 Docker 容器池之前的过渡隔离（默认后端）：每次执行在一个新建的临时目录内
进行（cwd 锁死），结束后目录「阅后即焚」删除，不残留脚本、字节码或中间文件。

两条与历史实现不同的约定：

1. **超时也要留下证据**。旧实现用 ``subprocess.run(timeout=...)``，超时后整段
   stdout 一起丢，模型只能猜「是不是没网」。现在把 stdout/stderr 落到沙盒内
   文件，杀进程后照样能读出来，作为 ``SandboxTimeoutError.partial_output`` 返回。
2. **网络与依赖受统一策略控制**。断网时注入黑洞代理 + ``PIP_NO_INDEX``（协作式，
   非内核级）；装依赖只允许白名单内的纯 Python 轮子，装进沙盒自己的 ``.deps``。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path

from app.tools.sandbox import dependencies as dep_policy
from app.tools.sandbox.provider import (
    SandboxDependencyRejected,
    SandboxExecutionError,
    SandboxResult,
    SandboxSpec,
    SandboxTimeoutError,
)
from app.tools.sandbox.workspace_bridge import detect_workspace, sandbox_env
from app.tools.sandbox_dir import make_sandbox, purge, redact_paths, safe_env

_MAX_OUTPUT = 8000
_STDOUT_LOG = "_stdout.log"
_STDERR_LOG = "_stderr.log"
_INSTALL_LOG = "_install.log"


class SubprocessSandboxProvider:
    """无状态后端；每次 run 都创建新临时目录并阅后即焚。"""

    def __init__(self, settings=None) -> None:
        self._settings = settings

    def run(self, spec: SandboxSpec) -> SandboxResult:
        sandbox = make_sandbox(spec.sandbox_prefix)
        try:
            self._write_files(sandbox, spec.files)
            if spec.dependencies:
                self._install(sandbox, spec)
            completed = self._execute(sandbox, spec)
            output = redact_paths(
                f"{completed['stdout']}{completed['stderr']}",
                sandbox,
                script_name=spec.redact_script_name,
            )
            return SandboxResult(
                exit_code=completed["exit_code"],
                output=output[:_MAX_OUTPUT],
                truncated=len(output) > _MAX_OUTPUT,
            )
        finally:
            purge(sandbox)

    # ---- 环境 ----

    def _base_env(self, spec: SandboxSpec) -> dict[str, str]:
        settings = self._settings
        network = True if spec.network is None else bool(spec.network)
        if spec.dependencies:
            # 装包必须联网，否则 plan() 已经拒绝；这里保持显式。
            network = True
        extra = dict(spec.env)
        if settings is not None:
            bridge = detect_workspace(settings)
            extra = {**sandbox_env(settings, bridge, offline=not network), **extra}
        elif not network:
            from app.tools.sandbox.workspace_bridge import _offline_env

            extra = {**_offline_env(), **extra}
        return safe_env(extra)

    # ---- 依赖 ----

    def _install(self, sandbox: Path, spec: SandboxSpec) -> None:
        settings = self._settings
        if settings is None:
            raise SandboxExecutionError()
        # 宿主解释器已经有的包不必再装：local 后端与宿主共用 site-packages，
        # 技能声明的依赖在生产机上通常早已随 requirements 装好，省掉一次解压。
        pending = _missing_from_host(spec.dependencies)
        if not pending:
            return
        try:
            plan = dep_policy.plan(
                pending,
                settings,
                trusted=spec.dependencies_trusted,
            )
        except dep_policy.DependencyRejected as exc:
            raise SandboxDependencyRejected(exc.code, exc.message) from None

        target = sandbox / dep_policy.DEPS_DIR
        target.mkdir(parents=True, exist_ok=True)
        env = self._base_env(SandboxSpec(argv=[], network=True))
        env["PYTHONPATH"] = str(target)
        argv = dep_policy.pip_argv(sys.executable, plan, target)
        log = sandbox / _INSTALL_LOG
        try:
            with log.open("w", encoding="utf-8", errors="replace") as handle:
                subprocess.run(
                    argv,
                    cwd=str(sandbox),
                    shell=False,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    timeout=plan.timeout,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    check=False,
                )
        except subprocess.TimeoutExpired:
            raise SandboxDependencyRejected(
                "DEPENDENCY_TIMEOUT",
                f"安装依赖超过 {int(plan.timeout)} 秒，已中止。请改用已有工具实现。",
            ) from None
        except OSError:
            raise SandboxExecutionError() from None

        size = dep_policy.installed_bytes(target)
        if size > plan.max_bytes:
            raise SandboxDependencyRejected(
                "DEPENDENCY_TOO_LARGE",
                f"依赖体积超过 {plan.max_bytes // (1024 * 1024)}MB 上限，已拒绝。",
            )
        # 装失败时 pip 退出码非 0，但没有现成轮子最常见：把日志末尾给模型看。
        if not any(target.iterdir()):
            tail = _tail(log.read_text(encoding="utf-8", errors="replace"), 600)
            if plan.offline_from_cache:
                raise SandboxDependencyRejected(
                    "DEPENDENCY_NOT_WARMED",
                    "预热缓存里没有这些轮子。请运维执行 "
                    "`python scripts/warm_sandbox_wheels.py` 后重试。"
                    f"{tail}".strip(),
                )
            raise SandboxDependencyRejected(
                "DEPENDENCY_UNAVAILABLE",
                f"没有可用的预编译轮子，已放弃编译。{tail}".strip(),
            )

    # ---- 执行 ----

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

    def _execute(self, sandbox: Path, spec: SandboxSpec) -> dict[str, object]:
        resolved = self._resolve(spec.argv)
        env = self._base_env(spec)
        if spec.dependencies:
            env["PYTHONPATH"] = str(sandbox / dep_policy.DEPS_DIR)
        out_path = sandbox / _STDOUT_LOG
        err_path = sandbox / _STDERR_LOG
        try:
            with out_path.open("wb") as out, err_path.open("wb") as err:
                process = subprocess.Popen(
                    resolved,
                    cwd=str(sandbox),
                    shell=False,
                    stdout=out,
                    stderr=err,
                    stdin=subprocess.DEVNULL,
                    env=env,
                )
                try:
                    code = process.wait(timeout=spec.timeout)
                except subprocess.TimeoutExpired:
                    _terminate(process)
                    raise SandboxTimeoutError(_read_output(out_path, err_path)) from None
        except OSError:
            raise SandboxExecutionError() from None
        return {
            "exit_code": code,
            "stdout": out_path.read_text(encoding="utf-8", errors="replace") if out_path.exists() else "",
            "stderr": err_path.read_text(encoding="utf-8", errors="replace") if err_path.exists() else "",
        }


def _terminate(process: subprocess.Popen) -> None:
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _read_output(out_path: Path, err_path: Path) -> str:
    parts: list[str] = []
    for path in (out_path, err_path):
        try:
            if path.exists():
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "".join(parts)[-_MAX_OUTPUT:]


def _tail(text: str, limit: int) -> str:
    text = text.strip()
    return text[-limit:] if len(text) > limit else text


_PINNED = re.compile(r"[<>=!~]")


def _missing_from_host(dependencies: tuple[str, ...]) -> tuple[str, ...]:
    """过滤掉宿主解释器里已经装好的包（同名、未指定版本时才算命中）。

    local 后端用的是宿主解释器，生产机上技能依赖多半已随 requirements 装好。
    这既省一次解压，也避免因为缓存没预热而把本来能跑的技能判成失败。
    """
    pending: list[str] = []
    for item in dependencies:
        name = str(item or "").strip()
        if not name:
            continue
        if not _PINNED.search(name):
            try:
                metadata.distribution(name)
            except (metadata.PackageNotFoundError, ValueError):
                pending.append(name)
            else:
                continue
        else:
            pending.append(name)
    return tuple(pending)
