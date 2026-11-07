"""短期文件下载。令牌是访问能力，不写入日志。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from app.core.exceptions import FileDownloadError
from app.data.tabular import DataError

router = APIRouter()


@router.get("/files/downloads/{token}", summary="下载数据处理产物")
def download_file(token: str, request: Request) -> FileResponse:
    store = request.app.state.data_store
    try:
        path, stored = store.resolve_download(token)
    except DataError as exc:
        raise FileDownloadError(exc.code) from None
    return FileResponse(
        path,
        media_type=stored.media_type,
        filename=stored.file_name,
    )
