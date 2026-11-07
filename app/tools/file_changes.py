"""工作区内的写入和删除。路径不能离开工作区，也不能指向目录。"""

from __future__ import annotations

import json
from pathlib import PurePath

from pydantic import BaseModel, ConfigDict, Field

from app.tools.file_sandbox import FileDenied, display_path, resolve_inside
from app.tools.file_tools import workspace_root
from app.tools.web_tools import CURRENT_SETTINGS

_MAX_CHARS = 100_000
_BINARY_SUFFIXES = {".doc", ".docx", ".pdf", ".ppt", ".pptx", ".xls", ".xlsx"}


class WriteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=240)
    content: str = Field(max_length=_MAX_CHARS)


class DeleteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=240)


def write_text_file(args: WriteArgs) -> str:
    try:
        if "\x00" in args.content:
            raise FileDenied()
        suffix = PurePath(args.path).suffix.lower()
        if suffix in _BINARY_SUFFIXES:
            message = "Word 文档请使用 write_docx" if suffix in {".doc", ".docx"} else "该格式不能当文本写入"
            return json.dumps(
                {"source": "workspace", "error": {"code": "file_unavailable", "message": message}},
                ensure_ascii=False,
            )
        root = workspace_root(CURRENT_SETTINGS.get())
        target = resolve_inside(root, args.path)
        if target == root or target.is_dir():
            raise FileDenied()
        parent = target.parent
        parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        data = args.content.encode("utf-8")
        target.write_bytes(data)
        return json.dumps(
            {
                "source": display_path(root, target),
                "bytes": len(data),
                "created": not existed,
            },
            ensure_ascii=False,
        )
    except FileDenied:
        return _unavailable()
    except OSError:
        return _unavailable()


def delete_file(args: DeleteArgs) -> str:
    try:
        root = workspace_root(CURRENT_SETTINGS.get())
        target = resolve_inside(root, args.path)
        if target == root or not target.is_file():
            raise FileDenied()
        target.unlink()
        return json.dumps(
            {"source": display_path(root, target), "deleted": True},
            ensure_ascii=False,
        )
    except FileDenied:
        return _unavailable()
    except OSError:
        return _unavailable()


def _unavailable() -> str:
    return json.dumps(
        {
            "source": "workspace",
            "error": {"code": "file_unavailable", "message": "路径不可用"},
        },
        ensure_ascii=False,
    )
