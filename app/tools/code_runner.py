"""在隔离临时目录里执行一段 Python。

无 shell、超时后结束、输出里不保留本机路径。每次执行都在一个新建的
临时目录内进行（cwd 锁死在该目录），结束后整个目录「阅后即焚」删除，
不残留脚本、字节码或中间文件。这是 Docker 容器池之前的过渡隔离：
相对路径读写只落在临时目录，绝对路径逃逸由后续容器方案兜底。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.tools.sandbox_dir import make_sandbox, purge, redact_paths, safe_env
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
    sandbox: Path | None = None
    try:
        settings = CURRENT_SETTINGS.get()
        if settings is None:
            return _error("code_unavailable", "代码执行暂不可用")
        timeout = settings.tools_sandbox_timeout_seconds
        sandbox = make_sandbox("muzi_py_")
        script = sandbox / "main.py"
        script.write_text(args.code, encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, "-I", "-P", "-u", str(script)],
            cwd=str(sandbox),
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=safe_env(),
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _error("code_timeout", "代码执行超时")
    except OSError:
        return _error("code_failed", "代码执行失败")
    finally:
        purge(sandbox)
    output = redact_paths(f"{completed.stdout}{completed.stderr}", sandbox, script_name="main.py")
    return json.dumps(
        {
            "language": "python",
            "exit_code": completed.returncode,
            "output": output[:_MAX_OUTPUT],
            "truncated": len(output) > _MAX_OUTPUT,
        },
        ensure_ascii=False,
    )


def _error(code: str, message: str) -> str:
    return json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False)
