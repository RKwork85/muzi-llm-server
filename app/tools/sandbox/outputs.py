"""沙盒唯一的可写产物出口。

约定：沙盒里只有 ``MUZI_OUTPUT`` 目录的写入会保留下来。工作区对沙盒是**只读
快照**（改它不会影响真实工作区），临时目录执行结束即销毁；需要交付的产物
（任何格式，包括 .xmind/.zip/.pdf/图片/音视频）都必须写进 ``MUZI_OUTPUT``，
再用 ``deliver_file`` 上传。

这里只做三件事：定位这个目录、执行前拍快照、执行后把新增文件报给模型。
不判断格式——格式由模型用代码生成，工具只负责让字节落地。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

OUTPUT_ENV = "MUZI_OUTPUT"
OUTPUT_DIRNAME = "_out"

_DEFAULT_MAX_FILES = 20
_DEFAULT_MAX_BYTES = 20 * 1024 * 1024


def output_dir(settings: Any, *, create: bool = True) -> Path | None:
    """当前用户工作区下的产物目录；工作区不可用或无法创建时返回 None。"""
    try:
        from app.tools.file_tools import workspace_root

        root = workspace_root(settings)
    except Exception:  # noqa: BLE001 - 工作区不可用一律降级，不能影响工具主流程
        return None
    target = root / OUTPUT_DIRNAME
    if create:
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
    return target if target.is_dir() else None


def snapshot(settings: Any) -> dict[str, int]:
    """执行前快照：文件名 → mtime_ns。只记录本层文件。"""
    directory = output_dir(settings)
    if directory is None:
        return {}
    found: dict[str, int] = {}
    try:
        for item in directory.iterdir():
            if not item.is_file():
                continue
            try:
                found[item.name] = item.stat().st_mtime_ns
            except OSError:
                continue
    except OSError:
        return {}
    return found


def collect(settings: Any, before: dict[str, int]) -> dict[str, object]:
    """把本次新产生/被覆盖的产物写进工具结果，方便模型直接交付。"""
    directory = output_dir(settings)
    if directory is None:
        return {}
    max_files = int(getattr(settings, "sandbox_output_max_files", _DEFAULT_MAX_FILES) or _DEFAULT_MAX_FILES)
    max_bytes = int(getattr(settings, "sandbox_output_max_bytes", _DEFAULT_MAX_BYTES) or _DEFAULT_MAX_BYTES)

    try:
        entries = sorted(directory.iterdir(), key=lambda path: path.name)
    except OSError:
        return {}

    items: list[dict[str, object]] = []
    skipped: list[str] = []
    total = 0
    for item in entries:
        if not item.is_file():
            continue
        try:
            size = item.stat().st_size
            stamp = item.stat().st_mtime_ns
        except OSError:
            continue
        if before.get(item.name) == stamp:
            continue
        if len(items) >= max_files:
            skipped.append(item.name)
            continue
        if total + size > max_bytes:
            skipped.append(item.name)
            continue
        total += size
        items.append(
            {
                "path": f"{OUTPUT_DIRNAME}/{item.name}",
                "file_name": item.name,
                "file_size": size,
            }
        )

    payload: dict[str, object] = {}
    if items:
        payload["outputs"] = items
        payload["outputs_hint"] = (
            "这些文件已经落在工作区产物目录，可以直接交付：调用 deliver_file，"
            f"path 传 \"{OUTPUT_DIRNAME}/文件名\"（只传文件名也会先在这里找）。"
        )
    if skipped:
        payload["outputs_skipped"] = skipped
        payload["outputs_skip_reason"] = (
            f"超出产物数量或体积上限（{max_files} 个 / {max_bytes} 字节），"
            "请清理不必要的中间文件后重试。"
        )
    return payload
