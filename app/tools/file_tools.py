"""工作区内的只读文件工具。不做写入、删除、移动或执行。"""

from __future__ import annotations

import json
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import ROOT, Settings
from app.tools.file_sandbox import (
    MAX_DIRECTORY_DEPTH,
    MAX_SCAN_FILES,
    MAX_SNIPPET_CHARS,
    FileDenied,
    FileTooLarge,
    blocked_name,
    confined,
    display_path,
    load_text,
    resolve_inside,
)
from app.tools.web_client import CURRENT_TOOL_BUDGET
from app.tools.web_tools import CURRENT_SETTINGS

_UNAVAILABLE = "路径不可用"


class ListArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(default=".", min_length=1, max_length=240)
    max_entries: int = Field(default=100, ge=1, le=200)


class ReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=240)
    max_chars: int = Field(default=8000, ge=1, le=20000)
    start_line: int = Field(default=1, ge=1, le=100_000)


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=100)
    path: str = Field(default=".", min_length=1, max_length=240)
    max_matches: int = Field(default=20, ge=1, le=50)


def workspace_root(settings: Settings | None) -> Path:
    if settings is None:
        raise FileDenied()
    raw = settings.tools_workspace_root.strip()
    if not raw:
        raise FileDenied()
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise FileDenied() from exc
    if resolved == ROOT or not resolved.is_dir():
        raise FileDenied()
    return resolved


def list_directory(args: ListArgs) -> str:
    try:
        root = workspace_root(CURRENT_SETTINGS.get())
        target = resolve_inside(root, args.path)
        if not target.is_dir():
            raise FileDenied()
        rows: list[dict[str, object]] = []
        truncated = False
        try:
            children = sorted(target.iterdir(), key=lambda item: item.name.lower())
        except OSError as exc:
            raise FileDenied() from exc
        for child in children:
            if blocked_name(child.name):
                continue
            try:
                resolved = child.resolve()
            except OSError:
                continue
            if not confined(root, resolved):
                continue
            if resolved.is_dir():
                size: int | None = None
                kind = "dir"
            elif resolved.is_file():
                kind = "file"
                try:
                    size = resolved.stat().st_size
                except OSError:
                    continue
            else:
                continue
            if len(rows) >= args.max_entries:
                truncated = True
                break
            rows.append({"name": child.name, "type": kind, "size": size})
        return _ok(
            {
                "source": display_path(root, target),
                "entries": rows,
                "truncated": truncated,
                "untrusted": True,
            }
        )
    except FileDenied:
        return _unavailable()


def read_text_file(args: ReadArgs) -> str:
    try:
        root = workspace_root(CURRENT_SETTINGS.get())
        target = resolve_inside(root, args.path)
        if not target.is_file():
            raise FileDenied()
        text = load_text(target)
        lines = text.splitlines()
        chosen = lines[args.start_line - 1 :]
        chunks: list[str] = []
        used = 0
        truncated = False
        for line in chosen:
            piece = line + "\n"
            if used + len(piece) > args.max_chars:
                remain = args.max_chars - used
                if remain > 0:
                    chunks.append(piece[:remain])
                truncated = True
                break
            chunks.append(piece)
            used += len(piece)
        return _ok(
            {
                "source": display_path(root, target),
                "content": "".join(chunks),
                "truncated": truncated,
                "total_lines": len(lines),
                "original_chars": len(text),
                "untrusted": True,
            }
        )
    except FileTooLarge:
        return _too_large()
    except FileDenied:
        return _unavailable()


def search_text(args: SearchArgs) -> str:
    try:
        root = workspace_root(CURRENT_SETTINGS.get())
        target = resolve_inside(root, args.path)
        if not target.exists():
            raise FileDenied()
        deadline = time.monotonic() + max(CURRENT_TOOL_BUDGET.get(), 0)
        if time.monotonic() >= deadline:
            return _ok(
                {
                    "source": display_path(root, target),
                    "query": args.query,
                    "matches": [],
                    "scanned_files": 0,
                    "truncated": True,
                    "untrusted": True,
                }
            )
        matches: list[dict[str, object]] = []
        scanned = 0
        truncated = False
        for path in _files(root, target, deadline):
            if time.monotonic() >= deadline:
                truncated = True
                break
            scanned += 1
            if scanned > MAX_SCAN_FILES:
                truncated = True
                break
            try:
                text = load_text(path)
            except (FileDenied, FileTooLarge):
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if args.query not in line:
                    continue
                matches.append(
                    {
                        "path": display_path(root, path),
                        "line": number,
                        "text": line[:MAX_SNIPPET_CHARS],
                        "truncated": len(line) > MAX_SNIPPET_CHARS,
                    }
                )
                if len(matches) >= args.max_matches:
                    truncated = True
                    break
            if truncated:
                break
        return _ok(
            {
                "source": display_path(root, target),
                "query": args.query,
                "matches": matches,
                "scanned_files": min(scanned, MAX_SCAN_FILES),
                "truncated": truncated,
                "untrusted": True,
            }
        )
    except FileDenied:
        return _unavailable()


def _files(root: Path, target: Path, deadline: float):
    if time.monotonic() >= deadline:
        return
    if target.is_file():
        yield target
        return
    if not target.is_dir():
        raise FileDenied()
    stack = [(target, 0)]
    while stack:
        if time.monotonic() >= deadline:
            return
        current, depth = stack.pop()
        try:
            children = sorted(current.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            continue
        for child in children:
            if blocked_name(child.name):
                continue
            try:
                resolved = child.resolve()
            except OSError:
                continue
            if not confined(root, resolved):
                continue
            if resolved.is_dir():
                if depth + 1 <= MAX_DIRECTORY_DEPTH:
                    stack.append((resolved, depth + 1))
                continue
            if resolved.is_file():
                yield resolved


def _ok(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _unavailable() -> str:
    return json.dumps(
        {
            "source": "workspace",
            "error": {"code": "file_unavailable", "message": _UNAVAILABLE},
            "untrusted": True,
            "truncated": False,
        },
        ensure_ascii=False,
    )


def _too_large() -> str:
    return json.dumps(
        {
            "source": "workspace",
            "error": {"code": "file_too_large", "message": "文件超出大小上限"},
            "untrusted": True,
            "truncated": True,
        },
        ensure_ascii=False,
    )
