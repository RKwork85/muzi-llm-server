"""工作区内的写入和删除。路径不能离开工作区，也不能指向目录。"""

from __future__ import annotations

import json
from pathlib import PurePath

from pydantic import BaseModel, ConfigDict, Field

from app.tools.file_sandbox import FileDenied, display_path, resolve_inside
from app.tools.file_tools import workspace_root
from app.tools.web_tools import CURRENT_SETTINGS

_MAX_CHARS = 100_000
# 这些后缀本质是二进制容器（压缩包/图片/音视频/Office），用文本写入必然产出坏文件。
# 过去只挡了 Office，导致模型可以把 JSON 文本写成 .xmind 交付出去，用户打开只会看到
# 「文件已损坏」。二进制产物应该走 run_python 写进 MUZI_OUTPUT，再用 deliver_file 交付。
_BINARY_SUFFIXES = frozenset(
    {
        ".doc",
        ".docx",
        ".pdf",
        ".ppt",
        ".pptx",
        ".xls",
        ".xlsx",
        ".xmind",
        ".zip",
        ".7z",
        ".rar",
        ".tar",
        ".gz",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".tif",
        ".tiff",
        ".mp3",
        ".wav",
        ".mp4",
        ".mov",
        ".avi",
    }
)


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
            if suffix in {".doc", ".docx"}:
                message = "Word 文档请使用 write_docx"
            else:
                message = (
                    "该格式是二进制容器，不能用文本写入；请在 run_python 里生成文件并写入 "
                    "MUZI_OUTPUT 目录，再用 deliver_file 交付。不要用文本冒充二进制格式。"
                )
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
