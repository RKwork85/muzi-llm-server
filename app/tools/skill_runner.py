"""执行技能入口脚本：复用 subprocess 沙盒（临时目录 + cwd 锁死 + 阅后即焚）。

skills 层负责发现、加载与编排，本模块承载真正落地的脚本执行，避免把
``subprocess`` 泄漏进 skills 数据层。脚本及其同目录资源从技能包复制到沙盒
临时目录，``args`` 序列化为 ``args.json`` 写入沙盒工作目录，脚本通过
``open("args.json")`` 读取输入，执行结束后整个目录删除。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.tools.sandbox_dir import make_sandbox, purge, redact_paths, safe_env

_MAX_OUTPUT = 8000


def run_skill_script(
    package: Path,
    entrypoint_rel: str,
    args: dict[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]:
    """在沙盒里执行技能入口脚本，返回 ``{exit_code, output, truncated}``。

    执行超时或失败时返回 ``{error: {code, message}}``，不会抛异常，调用方
    无需感知 subprocess 细节。
    """
    sandbox = make_sandbox("muzi_skill_")
    completed: subprocess.CompletedProcess[str] | None = None
    try:
        _copy_package(package, sandbox)
        (sandbox / "args.json").write_text(json.dumps(args, ensure_ascii=False), encoding="utf-8")
        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-P", "-u", str(sandbox / entrypoint_rel)],
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
            return {"error": {"code": "SKILL_EXECUTION_TIMEOUT", "message": "技能执行超时"}}
        except OSError:
            return {"error": {"code": "SKILL_EXECUTION_FAILED", "message": "技能执行失败"}}
        output = redact_paths(f"{completed.stdout}{completed.stderr}", sandbox)
        return {
            "exit_code": completed.returncode,
            "output": output[:_MAX_OUTPUT],
            "truncated": len(output) > _MAX_OUTPUT,
        }
    finally:
        purge(sandbox)


def _copy_package(package: Path, sandbox: Path) -> None:
    for path in package.rglob("*"):
        if not path.is_file() or path.name == "SKILL.md":
            continue
        relative = path.relative_to(package)
        target = sandbox / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
