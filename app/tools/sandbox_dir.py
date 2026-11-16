"""沙盒临时目录的创建、路径脱敏与阅后即焚。

代码/命令执行器每次运行都在一个新建的临时目录内进行（cwd 锁死），
结束后整个目录删除。这是 Docker 容器池之前的过渡隔离；绝对路径逃逸
由后续容器方案兜底，这里只保证相对路径不落在宿主机工作区、且不残留。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_KEPT_ENV_KEYS = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PATH", "TEMP", "TMP")


def make_sandbox(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


def purge(sandbox: Path | None) -> None:
    if sandbox is not None:
        shutil.rmtree(sandbox, ignore_errors=True)


def safe_env() -> dict[str, str]:
    """只保留子进程运行所需的最小环境变量，隔离宿主凭据。"""
    kept: dict[str, str] = {}
    for key in _KEPT_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            kept[key] = value
    kept["PYTHONIOENCODING"] = "utf-8"
    kept["PYTHONDONTWRITEBYTECODE"] = "1"
    kept["PYTHONNOUSERSITE"] = "1"
    return kept


def redact_paths(text: str, sandbox: Path | None, *, script_name: str = "") -> str:
    """把沙盒目录路径从输出里抹掉，避免泄露宿主机本机路径。"""
    if sandbox is None:
        return text
    cleaned = text
    for pattern, replacement in (
        (str(sandbox), "."),
        (sandbox.as_posix(), "."),
    ):
        cleaned = cleaned.replace(pattern, replacement)
    if script_name:
        script = sandbox / script_name
        for pattern in (str(script), script.as_posix()):
            cleaned = cleaned.replace(pattern, script_name)
    return cleaned
