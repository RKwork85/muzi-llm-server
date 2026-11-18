"""在隔离临时目录里执行一段 Python。

无 shell、超时后结束、输出里不保留本机路径。每次执行都在一个新建的
临时目录内进行（cwd 锁死在该目录），结束后整个目录「阅后即焚」删除。

三个关键约定：

- **超时分级**：默认 10 秒够探测，纯计算可以显式申请更长（上限见
  ``tools_sandbox_timeout_max``）。脚本里塞 pip install 这类慢操作不该靠默认值硬扛。
- **超时保留输出**：被杀的脚本会带回已产生的 stdout/stderr，模型据此判断卡在哪一步，
  而不是靠猜「是不是没网」。
- **依赖受策略限制**：只能装白名单内的纯 Python 轮子，装进沙盒自己的 ``.deps``。
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.tools.sandbox import (
    SandboxDependencyRejected,
    SandboxExecutionError,
    SandboxSpec,
    SandboxTimeoutError,
    get_provider,
)
from app.tools.web_tools import CURRENT_SETTINGS

MAX_TIMEOUT_SECONDS = 60


class PythonArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=8000)
    # 探测用 10 秒足够；确有多步计算时显式申请，避免整段被默认值硬杀。
    timeout_seconds: int | None = Field(default=None, ge=5, le=MAX_TIMEOUT_SECONDS)
    dependencies: list[str] = Field(default_factory=list, max_length=3)

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
    # 有依赖时必须让 PYTHONPATH 生效：``-I`` 隐含 ``-E``（忽略 PYTHON* 环境变量），
    # 会把沙盒内 ``.deps`` 一起屏蔽掉，装了也 import 不到。无依赖时仍然用 ``-I`` 收紧。
    flags = ["-s", "-P", "-u"] if args.dependencies else ["-I", "-P", "-u"]
    spec = SandboxSpec(
        argv=["python", *flags, "main.py"],
        files={"main.py": args.code.encode("utf-8")},
        timeout=_timeout(settings, args.timeout_seconds),
        sandbox_prefix="muzi_py_",
        redact_script_name="main.py",
        network=getattr(settings, "sandbox_network_enabled", True),
        dependencies=tuple(args.dependencies),
    )
    try:
        result = get_provider(settings).run(spec)
    except SandboxTimeoutError as exc:
        return _error(
            "code_timeout",
            f"代码执行超过 {spec.timeout:.0f} 秒被中止；下面是中止前已产生的输出。",
            partial=exc.partial_output,
            timeout=spec.timeout,
        )
    except SandboxDependencyRejected as exc:
        return _error(exc.code, exc.message)
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


def _timeout(settings, requested: int | None) -> float:
    """请求值受配置上限约束；没请求时用默认值，同样不越过上限。"""
    default = float(getattr(settings, "tools_sandbox_timeout_seconds", 10) or 10)
    ceiling = float(getattr(settings, "tools_sandbox_timeout_max", MAX_TIMEOUT_SECONDS) or MAX_TIMEOUT_SECONDS)
    wanted = float(requested) if requested else default
    return max(1.0, min(wanted, ceiling))


def _error(code: str, message: str, *, partial: str = "", timeout: float = 0.0) -> str:
    payload: dict[str, object] = {"error": {"code": code, "message": message}}
    if partial.strip():
        payload["output"] = partial
        payload["partial"] = True
    if timeout:
        payload["timeout_seconds"] = timeout
    return json.dumps(payload, ensure_ascii=False)
