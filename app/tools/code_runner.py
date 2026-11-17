"""在隔离临时目录里执行一段 Python。

无 shell、超时后结束、输出里不保留本机路径。每次执行都在一个新建的
临时目录内进行（cwd 锁死在该目录），结束后整个目录「阅后即焚」删除，
不残留脚本、字节码或中间文件。这是 Docker 容器池之前的过渡隔离：
相对路径读写只落在临时目录，绝对路径逃逸由后续容器方案兜底。
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.tools.sandbox import (
    SandboxExecutionError,
    SandboxSpec,
    SandboxTimeoutError,
    get_provider,
)
from app.tools.web_tools import CURRENT_SETTINGS


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
    settings = CURRENT_SETTINGS.get()
    if settings is None:
        return _error("code_unavailable", "代码执行暂不可用")
    spec = SandboxSpec(
        argv=["python", "-I", "-P", "-u", "main.py"],
        files={"main.py": args.code.encode("utf-8")},
        timeout=settings.tools_sandbox_timeout_seconds,
        sandbox_prefix="muzi_py_",
        redact_script_name="main.py",
    )
    try:
        result = get_provider(settings).run(spec)
    except SandboxTimeoutError:
        return _error("code_timeout", "代码执行超时")
    except SandboxExecutionError:
        return _error("code_failed", "代码执行失败")
    return json.dumps(
        {
            "language": "python",
            "exit_code": result.exit_code,
            "output": result.output,
            "truncated": result.truncated,
        },
        ensure_ascii=False,
    )


def _error(code: str, message: str) -> str:
    return json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False)
