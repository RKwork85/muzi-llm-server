"""执行技能入口脚本：把技能包与 args 委托给沙盒后端执行。

skills 层负责发现、加载与编排，本模块承载真正落地的脚本执行，避免把
沙盒后端细节泄漏进 skills 数据层。脚本及其同目录资源从技能包收集到沙盒
工作目录，``args`` 序列化为 ``args.json`` 写入沙盒工作目录，脚本通过
``open("args.json")`` 读取输入，执行结束后整个沙盒销毁。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.tools.sandbox import (
    SandboxExecutionError,
    SandboxSpec,
    SandboxTimeoutError,
    get_provider,
)
from app.tools.web_tools import CURRENT_SETTINGS

_PKG = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9,._-]+\])?"
    r"(?:(?:==|>=|<=|~=|!=|<|>)[A-Za-z0-9._*+!-]+)?$"
)


def run_skill_script(
    package: Path,
    entrypoint_rel: str,
    args: dict[str, Any],
    *,
    timeout: float,
    dependencies: str = "",
    network: bool = False,
    memory_limit: int | None = None,
) -> dict[str, Any]:
    """在沙盒里执行技能入口脚本，返回 ``{exit_code, output, truncated}``。

    执行超时或失败时返回 ``{error: {code, message}}``，不会抛异常，调用方
    无需感知沙盒后端细节。``dependencies`` 是逗号分隔的 pip 包名，由容器
    后端在脚本执行前安装；本地进程后端忽略它。
    """
    pkgs = _parse_dependencies(dependencies)
    if pkgs is None:
        return {"error": {"code": "SKILL_DEPENDENCY_INVALID", "message": "依赖包名不合法"}}
    files = _package_files(package)
    files["args.json"] = json.dumps(args, ensure_ascii=False).encode("utf-8")
    spec = SandboxSpec(
        argv=["python", "-I", "-P", "-u", entrypoint_rel],
        files=files,
        timeout=timeout,
        sandbox_prefix="muzi_skill_",
        memory_mb=memory_limit,
        network=True if network else None,
        dependencies=pkgs,
    )
    try:
        result = get_provider(CURRENT_SETTINGS.get()).run(spec)
    except SandboxTimeoutError:
        return {"error": {"code": "SKILL_EXECUTION_TIMEOUT", "message": "技能执行超时"}}
    except SandboxExecutionError:
        return {"error": {"code": "SKILL_EXECUTION_FAILED", "message": "技能执行失败"}}
    return {
        "exit_code": result.exit_code,
        "output": result.output,
        "truncated": result.truncated,
    }


def _package_files(package: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in package.rglob("*"):
        if not path.is_file() or path.name == "SKILL.md":
            continue
        relative = path.relative_to(package)
        files[relative.as_posix()] = path.read_bytes()
    return files


def _parse_dependencies(raw: str) -> tuple[str, ...] | None:
    """解析逗号分隔的 pip 包名。任一非法则返回 None（拒绝执行）。"""
    pkgs: list[str] = []
    for part in raw.split(","):
        pkg = part.strip()
        if not pkg:
            continue
        if not _PKG.fullmatch(pkg):
            return None
        pkgs.append(pkg)
    return tuple(pkgs)
