"""在工作区里执行一段 Python。无 shell，超时后结束，输出里不保留本机路径。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.tools.file_sandbox import FileDenied
from app.tools.file_tools import workspace_root
from app.tools.web_tools import CURRENT_SETTINGS

_MAX_OUTPUT = 8000


class PythonArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=8000)

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("code")
        return value


def run_python(args: PythonArgs) -> str:
    script: Path | None = None
    try:
        settings = CURRENT_SETTINGS.get()
        root = workspace_root(settings)
        timeout = 10 if settings is None else settings.tools_sandbox_timeout_seconds
        script = _write_script(args.code)
        completed = subprocess.run(
            [sys.executable, "-I", "-P", "-u", str(script)],
            cwd=str(root),
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_safe_env(),
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except FileDenied:
        return _error("code_unavailable", "代码执行暂不可用")
    except subprocess.TimeoutExpired:
        return _error("code_timeout", "代码执行超时")
    except OSError:
        return _error("code_failed", "代码执行失败")
    finally:
        if script is not None:
            try:
                script.unlink(missing_ok=True)
            except OSError:
                pass
    output = _redact(f"{completed.stdout}{completed.stderr}", root, script)
    return json.dumps(
        {
            "language": "python",
            "exit_code": completed.returncode,
            "output": output[:_MAX_OUTPUT],
            "truncated": len(output) > _MAX_OUTPUT,
        },
        ensure_ascii=False,
    )


def _write_script(code: str) -> Path:
    handle = tempfile.NamedTemporaryFile(prefix="muzi_code_", suffix=".py", delete=False, mode="w", encoding="utf-8")
    try:
        handle.write(code)
    finally:
        handle.close()
    return Path(handle.name)


def _safe_env() -> dict[str, str]:
    kept: dict[str, str] = {}
    for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PATH", "TEMP", "TMP"):
        value = os.environ.get(key)
        if value:
            kept[key] = value
    kept["PYTHONIOENCODING"] = "utf-8"
    kept["PYTHONDONTWRITEBYTECODE"] = "1"
    kept["PYTHONNOUSERSITE"] = "1"
    return kept


def _redact(text: str, root: Path, script: Path) -> str:
    return text.replace(str(script), "main.py").replace(str(root), ".")


def _error(code: str, message: str) -> str:
    return json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False)
