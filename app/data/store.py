"""受控数据目录。对外只给引用，不给绝对路径。"""

from __future__ import annotations

import calendar
import hashlib
import json
import secrets
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from app.core.config import ROOT, Settings
from app.data.tabular import DataError

_ID = r"^[A-Za-z0-9_-]{8,64}$"


@dataclass
class StoredDataset:
    dataset_id: str
    columns: list[str]
    row_count: int
    data_format: str
    expires_at: str


@dataclass
class StoredFile:
    file_id: str
    file_name: str
    media_type: str
    file_size: int
    sha256: str
    kind: str
    manifest: list[dict[str, object]]
    expires_at: str


@dataclass
class DownloadGrant:
    token: str
    download_path: str
    expires_at: str
    file_name: str
    file_size: int
    sha256: str
    media_type: str


class DataStore:
    def __init__(
        self,
        root: Path,
        *,
        file_ttl_seconds: float,
        download_ttl_seconds: float,
        max_bytes: int,
        max_package_files: int,
        max_package_bytes: int,
    ) -> None:
        self.root = root
        self.file_ttl_seconds = file_ttl_seconds
        self.download_ttl_seconds = download_ttl_seconds
        self.max_bytes = max_bytes
        self.max_package_files = max_package_files
        self.max_package_bytes = max_package_bytes
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> DataStore:
        return cls(
            resolve_store_root(settings),
            file_ttl_seconds=settings.data_file_ttl_seconds,
            download_ttl_seconds=settings.data_download_ttl_seconds,
            max_bytes=settings.data_max_bytes,
            max_package_files=settings.data_max_package_files,
            max_package_bytes=settings.data_max_package_bytes,
        )

    def ready(self) -> bool:
        return self.root != ROOT and self.root.is_dir()

    def save_dataset(
        self,
        task_id: str,
        columns: list[str],
        rows: list[dict[str, Any]],
        data_format: str,
    ) -> StoredDataset:
        self._ensure_ready()
        payload = json.dumps({"columns": columns, "rows": rows}, ensure_ascii=False).encode("utf-8")
        if len(payload) > self.max_bytes:
            raise DataError("FILE_TOO_LARGE", "数据集超出大小上限")
        dataset_id = _new_id()
        self._write_task_file(task_id, "datasets", dataset_id, payload)
        expires = _stamp(self.file_ttl_seconds)
        record = {
            "columns": columns,
            "row_count": len(rows),
            "format": data_format,
            "expires_at": expires,
        }
        self._update_meta(task_id, "datasets", dataset_id, record)
        return StoredDataset(dataset_id, columns, len(rows), data_format, expires)

    def load_dataset(self, task_id: str, dataset_id: str) -> tuple[list[str], list[dict[str, Any]]]:
        self._ensure_ready()
        if not _safe_id(dataset_id):
            raise DataError("DATASET_ACCESS_DENIED", "数据集不存在或无权访问")
        meta = self._meta(task_id).get("datasets", {}).get(dataset_id)
        if not isinstance(meta, dict) or _expired(str(meta.get("expires_at") or "")):
            raise DataError("DATASET_ACCESS_DENIED", "数据集不存在或无权访问")
        raw = self._read_task_file(task_id, "datasets", dataset_id)
        payload = json.loads(raw.decode("utf-8"))
        return list(payload["columns"]), list(payload["rows"])

    def save_bytes(
        self,
        task_id: str,
        *,
        file_name: str,
        media_type: str,
        content: bytes,
        kind: str = "file",
        manifest: list[dict[str, object]] | None = None,
    ) -> StoredFile:
        self._ensure_ready()
        if len(content) > self.max_bytes:
            raise DataError("FILE_TOO_LARGE", "文件超出大小上限")
        file_id = _new_id()
        self._write_task_file(task_id, "files", file_id, content)
        expires = _stamp(self.file_ttl_seconds)
        record = StoredFile(
            file_id=file_id,
            file_name=file_name,
            media_type=media_type,
            file_size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            kind=kind,
            manifest=manifest or [],
            expires_at=expires,
        )
        self._update_meta(task_id, "files", file_id, _file_dict(record))
        return record

    def get_file(self, task_id: str, file_id: str) -> StoredFile:
        self._ensure_ready()
        meta = self._file_meta(task_id, file_id)
        return _file_from_meta(file_id, meta)

    def file_path(self, task_id: str, file_id: str) -> Path:
        self.get_file(task_id, file_id)
        path = self._task_dir(task_id) / "files" / file_id
        resolved = path.resolve()
        if not _inside(self.root, resolved) or not resolved.is_file():
            raise DataError("ARTIFACT_NOT_FOUND", "文件不存在或无权访问")
        return resolved

    def build_zip(
        self,
        task_id: str,
        entries: list[tuple[str, str]],
        package_name: str,
    ) -> StoredFile:
        self._ensure_ready()
        if not entries:
            raise DataError("PACKAGE_EMPTY", "数据包没有文件")
        if len(entries) > self.max_package_files:
            raise DataError("PACKAGE_LIMIT_EXCEEDED", "数据包文件数量超出上限")
        total = 0
        manifest: list[dict[str, object]] = []
        paths = [self.file_path(task_id, file_id) for file_id, _name in entries]
        for (file_id, arcname), source in zip(entries, paths, strict=True):
            size = source.stat().st_size
            total += size
            if total > self.max_package_bytes:
                raise DataError("PACKAGE_LIMIT_EXCEEDED", "数据包大小超出上限")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            manifest.append({"path": arcname, "size": size, "sha256": digest, "file_id": file_id})
        target = self._task_dir(task_id) / "files"
        target.mkdir(parents=True, exist_ok=True)
        file_id = _new_id()
        output = target / file_id
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as package:
            for (_file_id, arcname), source in zip(entries, paths, strict=True):
                with source.open("rb") as handle, package.open(arcname, "w") as target_handle:
                    _copy_stream(handle, target_handle)
        content = output.read_bytes()
        if len(content) > self.max_bytes:
            output.unlink(missing_ok=True)
            raise DataError("FILE_TOO_LARGE", "压缩包超出大小上限")
        expires = _stamp(self.file_ttl_seconds)
        record = StoredFile(
            file_id=file_id,
            file_name=package_name,
            media_type="application/zip",
            file_size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            kind="package",
            manifest=manifest,
            expires_at=expires,
        )
        self._update_meta(task_id, "files", file_id, _file_dict(record))
        return record

    def issue_download(self, task_id: str, file_id: str, *, ttl_seconds: float | None = None) -> DownloadGrant:
        stored = self.get_file(task_id, file_id)
        ttl = self.download_ttl_seconds if ttl_seconds is None else ttl_seconds
        expires_at = time.time() + ttl
        token = secrets.token_urlsafe(24)
        with self._lock:
            grants = self._grants()
            grants[token] = {"task_id": task_id, "file_id": file_id, "expires_at": expires_at}
            self._write_grants(grants)
        self.cleanup_expired()
        return DownloadGrant(
            token=token,
            download_path=f"/api/v1/files/downloads/{token}",
            expires_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires_at)),
            file_name=stored.file_name,
            file_size=stored.file_size,
            sha256=stored.sha256,
            media_type=stored.media_type,
        )

    def resolve_download(self, token: str) -> tuple[Path, StoredFile]:
        self._ensure_ready()
        if not _safe_id(token):
            raise DataError("DOWNLOAD_ACCESS_DENIED", "下载不存在或无权访问")
        with self._lock:
            grant = self._grants().get(token)
        if not isinstance(grant, dict):
            raise DataError("DOWNLOAD_ACCESS_DENIED", "下载不存在或无权访问")
        if float(grant.get("expires_at") or 0) <= time.time():
            raise DataError("DOWNLOAD_EXPIRED", "下载链接已过期")
        task_id = str(grant.get("task_id") or "")
        file_id = str(grant.get("file_id") or "")
        stored = self.get_file(task_id, file_id)
        return self.file_path(task_id, file_id), stored

    def cleanup_expired(self) -> int:
        if not self.ready():
            return 0
        removed = 0
        tasks = self.root / "tasks"
        if not tasks.is_dir():
            return 0
        for task_dir in tasks.iterdir():
            if not task_dir.is_dir() or not _inside(self.root, task_dir.resolve()):
                continue
            meta_path = task_dir / "meta.json"
            if not meta_path.is_file():
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            for bucket, folder in (("datasets", "datasets"), ("files", "files")):
                items = meta.get(bucket, {})
                if not isinstance(items, dict):
                    continue
                for item_id, item in list(items.items()):
                    if not isinstance(item, dict) or not _expired(str(item.get("expires_at") or "")):
                        continue
                    path = task_dir / folder / item_id
                    if path.is_file() and _inside(self.root, path.resolve()):
                        path.unlink()
                        removed += 1
                    items.pop(item_id, None)
            meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return removed

    def _ensure_ready(self) -> None:
        if not self.ready():
            raise DataError("STORAGE_UNAVAILABLE", "数据存储不可用")

    def _task_dir(self, task_id: str) -> Path:
        if not _safe_id(task_id):
            raise DataError("STORAGE_UNAVAILABLE", "任务标识无效")
        path = (self.root / "tasks" / task_id).resolve()
        if not _inside(self.root, path):
            raise DataError("STORAGE_UNAVAILABLE", "任务标识无效")
        return path

    def _write_task_file(self, task_id: str, folder: str, item_id: str, content: bytes) -> None:
        if not _safe_id(item_id):
            raise DataError("STORAGE_UNAVAILABLE", "文件标识无效")
        directory = self._task_dir(task_id) / folder
        directory.mkdir(parents=True, exist_ok=True)
        path = (directory / item_id).resolve()
        if not _inside(self.root, path):
            raise DataError("STORAGE_UNAVAILABLE", "文件标识无效")
        path.write_bytes(content)

    def _read_task_file(self, task_id: str, folder: str, item_id: str) -> bytes:
        path = (self._task_dir(task_id) / folder / item_id).resolve()
        if not _inside(self.root, path) or not path.is_file():
            raise DataError("DATASET_ACCESS_DENIED", "数据集不存在或无权访问")
        return path.read_bytes()

    def _meta(self, task_id: str) -> dict[str, Any]:
        path = self._task_dir(task_id) / "meta.json"
        if not path.is_file():
            return {"datasets": {}, "files": {}}
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("datasets", {})
        payload.setdefault("files", {})
        return payload

    def _update_meta(self, task_id: str, bucket: str, item_id: str, record: dict[str, Any]) -> None:
        with self._lock:
            meta = self._meta(task_id)
            meta[bucket][item_id] = record
            path = self._task_dir(task_id) / "meta.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    def _file_meta(self, task_id: str, file_id: str) -> dict[str, Any]:
        if not _safe_id(file_id):
            raise DataError("ARTIFACT_NOT_FOUND", "文件不存在或无权访问")
        meta = self._meta(task_id).get("files", {}).get(file_id)
        if not isinstance(meta, dict) or _expired(str(meta.get("expires_at") or "")):
            raise DataError("ARTIFACT_NOT_FOUND", "文件不存在或无权访问")
        return meta

    def _grants(self) -> dict[str, Any]:
        path = self.root / "downloads.json"
        if not path.is_file():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}

    def _write_grants(self, grants: dict[str, Any]) -> None:
        path = self.root / "downloads.json"
        path.write_text(json.dumps(grants, ensure_ascii=False), encoding="utf-8")


def resolve_store_root(settings: Settings) -> Path:
    raw = settings.data_store_root.strip()
    if not raw:
        return ROOT
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    try:
        resolved = path.resolve()
    except OSError:
        return ROOT
    return resolved


def store_is_ready(settings: Settings) -> bool:
    resolved = resolve_store_root(settings)
    return resolved != ROOT and resolved.is_dir()


def _new_id() -> str:
    return secrets.token_urlsafe(16)


def _safe_id(value: str) -> bool:
    import re

    return re.fullmatch(_ID, value) is not None


def _stamp(ttl: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + ttl))


def _expired(stamp: str) -> bool:
    try:
        parsed = time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return True
    return calendar.timegm(parsed) <= time.time()


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _copy_stream(source: BinaryIO, target: BinaryIO) -> None:
    while True:
        chunk = source.read(64 * 1024)
        if not chunk:
            return
        target.write(chunk)


def _file_dict(record: StoredFile) -> dict[str, Any]:
    return {
        "file_name": record.file_name,
        "media_type": record.media_type,
        "file_size": record.file_size,
        "sha256": record.sha256,
        "kind": record.kind,
        "manifest": record.manifest,
        "expires_at": record.expires_at,
    }


def _file_from_meta(file_id: str, meta: dict[str, Any]) -> StoredFile:
    return StoredFile(
        file_id=file_id,
        file_name=str(meta["file_name"]),
        media_type=str(meta["media_type"]),
        file_size=int(meta["file_size"]),
        sha256=str(meta["sha256"]),
        kind=str(meta.get("kind") or "file"),
        manifest=list(meta.get("manifest") or []),
        expires_at=str(meta["expires_at"]),
    )
