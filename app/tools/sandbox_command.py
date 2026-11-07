"""工作区内的命令。无 shell，命令名必须事先授权，参数不能逃出工作区。"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.tools.file_sandbox import FileDenied
from app.tools.file_tools import workspace_root
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
_MAX_OUTPUT = 8000


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
    try:
        settings = CURRENT_SETTINGS.get()
        root = workspace_root(settings)
        name = args.command.lower()
        if name in _HARD_DENY or name not in _allowlist(settings):
            return _error("command_not_authorized", "命令未授权")
        executable = shutil.which(args.command)
        if not executable:
            return _error("command_not_authorized", "命令未授权")
        timeout = 10 if settings is None else settings.tools_sandbox_timeout_seconds
        completed = subprocess.run(
            [executable, *args.args],
            cwd=str(root),
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_safe_env(),
            check=False,
        )
    except FileDenied:
        return _error("command_not_authorized", "命令未授权")
    except subprocess.TimeoutExpired:
        return _error("command_timeout", "命令超时")
    except OSError:
        return _error("command_failed", "命令执行失败")
    output = f"{completed.stdout}{completed.stderr}"
    truncated = len(output) > _MAX_OUTPUT
    return json.dumps(
        {
            "command": args.command,
            "exit_code": completed.returncode,
            "output": output[:_MAX_OUTPUT],
            "truncated": truncated,
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


def _safe_env() -> dict[str, str]:
    kept: dict[str, str] = {}
    for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PATH", "TEMP", "TMP"):
        value = os.environ.get(key)
        if value:
            kept[key] = value
    return kept


def _error(code: str, message: str) -> str:
    return json.dumps(
        {"error": {"code": code, "message": message}},
        ensure_ascii=False,
    )
