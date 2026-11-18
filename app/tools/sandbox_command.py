"""在隔离临时目录里执行授权命令。无 shell，命令名必须事先授权，参数不能逃出。

每次执行都在新建的临时目录内进行（cwd 锁死），结束后目录「阅后即焚」。
命令的默认工作位置不再是宿主机工作区，中间产物不会残留。
"""

from __future__ import annotations

import json
import re
import shutil

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.tools.sandbox import (
    SandboxExecutionError,
    SandboxSpec,
    SandboxTimeoutError,
    get_provider,
)
from app.tools.web_tools import CURRENT_SETTINGS

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}")
_HARD_DENY = frozenset(
    {
        "cmd",
        "powershell",
        "pwsh",
        "bash",
        "sh",
        "wscript",
        "cscript",
        "mshta",
        "certutil",
        "bitsadmin",
        "schtasks",
        "reg",
        "rundll32",
        "ssh",
        "scp",
        "ftp",
        "curl",
        "wget",
    }
)
_BLOCKED_ARGS = frozenset({"-c", "/c", "-command", "-encodedcommand", "--command"})


class CommandArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1, max_length=40)
    args: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("command")
    @classmethod
    def _command(cls, value: str) -> str:
        if _NAME.fullmatch(value) is None:
            raise ValueError("command")
        return value

    @field_validator("args")
    @classmethod
    def _args(cls, value: list[str]) -> list[str]:
        for item in value:
            if len(item) > 200 or "\x00" in item:
                raise ValueError("args")
            lowered = item.lower()
            if lowered in _BLOCKED_ARGS or any(char in item for char in "&|;<>\n\r"):
                raise ValueError("args")
            if ".." in item or item.startswith(("/", "\\")) or (len(item) > 1 and item[1] == ":"):
                raise ValueError("args")
        return value


def run_command(args: CommandArgs) -> str:
    settings = CURRENT_SETTINGS.get()
    if settings is None:
        return _error("command_not_authorized", "命令未授权")
    name = args.command.lower()
    if name in _HARD_DENY or name not in _allowlist(settings):
        return _error("command_not_authorized", "命令未授权")
    executable = shutil.which(args.command)
    if not executable:
        return _error("command_not_authorized", "命令未授权")
    spec = SandboxSpec(
        argv=[args.command, *args.args],
        timeout=settings.tools_sandbox_timeout_seconds,
        sandbox_prefix="muzi_cmd_",
        network=getattr(settings, "sandbox_network_enabled", True),
    )
    try:
        result = get_provider(settings).run(spec)
    except SandboxTimeoutError as exc:
        return _error("command_timeout", "命令超时", partial=exc.partial_output)
    except SandboxExecutionError:
        return _error("command_failed", "命令执行失败")
    return json.dumps(
        {
            "command": args.command,
            "exit_code": result.exit_code,
            "output": result.output,
            "truncated": result.truncated,
        },
        ensure_ascii=False,
    )


def _allowlist(settings: object) -> set[str]:
    raw = getattr(settings, "tools_sandbox_commands", "") or ""
    names: set[str] = set()
    for part in str(raw).split(","):
        name = part.strip().lower()
        if _NAME.fullmatch(name) and name not in _HARD_DENY:
            names.add(name)
    return names


def _error(code: str, message: str, *, partial: str = "") -> str:
    payload: dict[str, object] = {"error": {"code": code, "message": message}}
    if partial.strip():
        payload["output"] = partial
        payload["partial"] = True
    return json.dumps(payload, ensure_ascii=False)
