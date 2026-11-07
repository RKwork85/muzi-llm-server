"""把工作区文件上传到对象存储，并把地址交回模型。不发送本机绝对路径。"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from app.data.context import CURRENT_OSS_CLIENT, CURRENT_TASK_ID
from app.data.oss_client import OssUploadError
from app.tools.file_sandbox import FileDenied, display_path, resolve_inside
from app.tools.file_tools import workspace_root
from app.tools.web_tools import CURRENT_SETTINGS

USER_DOWNLOAD_KIND = "user_download"


class DeliverArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=240)


async def deliver_file(args: DeliverArgs) -> str:
    try:
        settings = CURRENT_SETTINGS.get()
        root = workspace_root(settings)
        target = resolve_inside(root, args.path)
        if target == root or not target.is_file():
            raise FileDenied()
        content = target.read_bytes()
        limit = 1_000_000 if settings is None else settings.data_max_bytes
        if len(content) > limit:
            return _error("FILE_TOO_LARGE", "文件超出大小上限")
        client = CURRENT_OSS_CLIENT.get()
        if client is None:
            raise OssUploadError("OSS_UNAVAILABLE", "上传服务不可用")
        task_id = CURRENT_TASK_ID.get() or "ask"
        urls = await client.upload_files([(target.name, content)], client.upload_path_for(task_id))
        return json.dumps(
            {
                "ok": True,
                "file_name": target.name,
                "source": display_path(root, target),
                "file_size": len(content),
                "download_url": urls[0],
                "kind": USER_DOWNLOAD_KIND,
                "summary": "把 download_url 原样发给用户",
            },
            ensure_ascii=False,
        )
    except FileDenied:
        return _error("file_unavailable", "路径不可用")
    except OssUploadError as exc:
        return _error(exc.code, exc.message)
    except OSError:
        return _error("file_unavailable", "路径不可用")


def _error(code: str, message: str) -> str:
    return json.dumps(
        {"ok": False, "error": {"code": code, "message": message}},
        ensure_ascii=False,
    )
