"""数据清洗、导出和打包。结果只含引用和摘要，不含文件字节。"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.data.context import CURRENT_DATA_STORE, CURRENT_OSS_CLIENT, CURRENT_TASK_ID
from app.data.oss_client import OssUploadError
from app.data.store import DataStore, StoredFile
from app.data.tabular import (
    DataError,
    classify_rows,
    clean_rows,
    deduplicate_rows,
    normalize_rows,
    parse_csv_table,
    parse_json_table,
    sanitize_cell,
    validate_rows,
)
from app.data.xlsx_writer import build_xlsx
from app.tools.file_sandbox import FileDenied, resolve_inside
from app.tools.file_tools import workspace_root
from app.tools.web_tools import CURRENT_SETTINGS

_NAME = re.compile(r"^[\w\u4e00-\u9fff][\w\u4e00-\u9fff._-]{0,80}$", re.UNICODE)
_ZIP_PART = re.compile(r"^[\w\u4e00-\u9fff][\w\u4e00-\u9fff._-]{0,80}$", re.UNICODE)


class ImportArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | None = Field(default=None, max_length=200_000)
    workspace_path: str | None = Field(default=None, min_length=1, max_length=240)
    data_format: str = Field(pattern=r"^(json|csv)$")
    delimiter: str = Field(default=",", max_length=1)


class CleanArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(min_length=8, max_length=64)
    drop_empty_rows: bool = True
    trim_strings: bool = True
    drop_if_blank: list[str] = Field(default_factory=list, max_length=32)


class DedupeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(min_length=8, max_length=64)
    columns: list[str] = Field(default_factory=list, max_length=32)


class NormalizeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(min_length=8, max_length=64)
    rename: dict[str, str] = Field(default_factory=dict)
    strip_columns: list[str] = Field(default_factory=list, max_length=32)
    date_columns: list[str] = Field(default_factory=list, max_length=32)
    number_columns: list[str] = Field(default_factory=list, max_length=32)


class ClassifyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(min_length=8, max_length=64)
    column: str = Field(min_length=1, max_length=64)
    rules: list[dict[str, str]] = Field(min_length=1, max_length=32)
    default_label: str = Field(default="其他", min_length=1, max_length=64)
    output_column: str = Field(default="category", min_length=1, max_length=64)


class ValidateDataArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(min_length=8, max_length=64)
    required: list[str] = Field(default_factory=list, max_length=32)
    types: dict[str, str] = Field(default_factory=dict)
    enums: dict[str, list[str]] = Field(default_factory=dict)


class ExportArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(min_length=8, max_length=64)
    file_name: str = Field(min_length=1, max_length=80)


class ExportCsvArgs(ExportArgs):
    encoding: str = Field(default="utf-8-sig", pattern=r"^(utf-8|utf-8-sig)$")
    delimiter: str = Field(default=",", max_length=1)


class ExportExcelArgs(ExportArgs):
    sheet_name: str = Field(default="数据", min_length=1, max_length=31)


class ExportMarkdownArgs(ExportArgs):
    title: str = Field(default="数据", min_length=1, max_length=80)
    columns: list[str] = Field(default_factory=list, max_length=32)


class PackageEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: str = Field(min_length=8, max_length=64)
    path: str = Field(min_length=1, max_length=160)


class PackageArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[PackageEntry] = Field(min_length=1, max_length=20)
    file_name: str = Field(min_length=1, max_length=80)


class PackageQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_id: str = Field(min_length=8, max_length=64)


class OssFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: str = Field(min_length=8, max_length=64)


class OssFilesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_ids: list[str] = Field(min_length=1, max_length=20)


class DownloadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: str = Field(min_length=8, max_length=64)


def import_dataset(args: ImportArgs) -> str:
    try:
        text = _load_text(args)
        columns, rows = _parse(text, args)
        saved = _store().save_dataset(_task_id(), columns, rows, args.data_format)
        return _dataset_result(saved.dataset_id, columns, rows, "已读入数据集")
    except DataError as exc:
        return _failure(exc)


def clean_data(args: CleanArgs) -> str:
    try:
        columns, rows = _store().load_dataset(_task_id(), args.dataset_id)
        kept, removed = clean_rows(
            columns,
            rows,
            drop_empty_rows=args.drop_empty_rows,
            trim_strings=args.trim_strings,
            drop_if_blank=args.drop_if_blank,
        )
        saved = _store().save_dataset(_task_id(), columns, kept, "json")
        return _dataset_result(saved.dataset_id, columns, kept, f"删除 {removed} 行")
    except DataError as exc:
        return _failure(exc)


def deduplicate_data(args: DedupeArgs) -> str:
    try:
        columns, rows = _store().load_dataset(_task_id(), args.dataset_id)
        kept, removed = deduplicate_rows(columns, rows, args.columns)
        saved = _store().save_dataset(_task_id(), columns, kept, "json")
        return _dataset_result(saved.dataset_id, columns, kept, f"去重删除 {removed} 行")
    except DataError as exc:
        return _failure(exc)


def normalize_data(args: NormalizeArgs) -> str:
    try:
        if len(args.rename) > 32:
            raise DataError("INVALID_TRANSFORM_RULE", "重命名字段过多")
        columns, rows = _store().load_dataset(_task_id(), args.dataset_id)
        new_columns, kept = normalize_rows(
            columns,
            rows,
            rename=args.rename,
            strip_columns=args.strip_columns,
            date_columns=args.date_columns,
            number_columns=args.number_columns,
        )
        saved = _store().save_dataset(_task_id(), new_columns, kept, "json")
        return _dataset_result(saved.dataset_id, new_columns, kept, "已标准化字段")
    except DataError as exc:
        return _failure(exc)


def classify_data(args: ClassifyArgs) -> str:
    try:
        columns, rows = _store().load_dataset(_task_id(), args.dataset_id)
        new_columns, kept, counts = classify_rows(
            columns,
            rows,
            column=args.column,
            rules=args.rules,
            default_label=args.default_label,
            output_column=args.output_column,
        )
        saved = _store().save_dataset(_task_id(), new_columns, kept, "json")
        body = json.loads(_dataset_result(saved.dataset_id, new_columns, kept, "已按规则分类"))
        body["counts"] = counts
        return json.dumps(body, ensure_ascii=False)
    except DataError as exc:
        return _failure(exc)


def validate_data(args: ValidateDataArgs) -> str:
    try:
        columns, rows = _store().load_dataset(_task_id(), args.dataset_id)
        issues = validate_rows(columns, rows, required=args.required, types=args.types, enums=args.enums)
        return json.dumps(
            {
                "ok": not issues,
                "dataset_id": args.dataset_id,
                "error": None
                if not issues
                else {"code": "DATA_VALIDATION_FAILED", "message": "数据校验未通过"},
                "issue_count": len(issues),
                "issues": issues,
                "row_count": len(rows),
                "columns": columns,
                "untrusted": True,
            },
            ensure_ascii=False,
        )
    except DataError as exc:
        return _failure(exc)


def export_json(args: ExportArgs) -> str:
    return _export(args.dataset_id, args.file_name, ".json", "application/json", "export_json", lambda columns, rows: _json_bytes(rows))


def export_csv(args: ExportCsvArgs) -> str:
    if args.delimiter not in {",", ";", "\t"}:
        return _failure(DataError("INVALID_TRANSFORM_RULE", "分隔符不在允许列表中"))

    def render(columns: list[str], rows: list[dict[str, Any]]) -> bytes:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, delimiter=args.delimiter, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _csv_value(row.get(column)) for column in columns})
        return buffer.getvalue().encode(args.encoding)

    return _export(args.dataset_id, args.file_name, ".csv", "text/csv", "export_csv", render)


def export_excel(args: ExportExcelArgs) -> str:
    try:
        sheet = _sheet_name(args.sheet_name)
    except DataError as exc:
        return _failure(exc)

    def render(columns: list[str], rows: list[dict[str, Any]]) -> bytes:
        table = [[sanitize_cell(row.get(column)) for column in columns] for row in rows]
        return build_xlsx(sheet, columns, table)

    return _export(
        args.dataset_id,
        args.file_name,
        ".xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "export_excel",
        render,
    )


def export_markdown(args: ExportMarkdownArgs) -> str:
    def render(columns: list[str], rows: list[dict[str, Any]]) -> bytes:
        selected = args.columns or columns
        missing = [item for item in selected if item not in columns]
        if missing:
            raise DataError("INVALID_COLUMN", "列不存在")
        lines = [f"# {args.title}", "", "| " + " | ".join(_md(item) for item in selected) + " |"]
        lines.append("| " + " | ".join("---" for _ in selected) + " |")
        for row in rows:
            lines.append("| " + " | ".join(_md(row.get(column)) for column in selected) + " |")
        return ("\n".join(lines) + "\n").encode("utf-8")

    return _export(args.dataset_id, args.file_name, ".md", "text/markdown", "export_markdown", render)


def create_package(args: PackageArgs) -> str:
    try:
        file_name = _file_name(args.file_name, ".zip")
        entries: list[tuple[str, str]] = []
        seen: set[str] = set()
        for item in args.files:
            arcname = _zip_path(item.path)
            if arcname in seen:
                raise DataError("PACKAGE_BUILD_FAILED", "压缩包内路径重复")
            seen.add(arcname)
            entries.append((item.file_id, arcname))
        stored = _store().build_zip(_task_id(), entries, file_name)
        return _file_result(stored, "已生成数据包")
    except DataError as exc:
        return _failure(exc)


def list_package_files(args: PackageQuery) -> str:
    try:
        stored = _store().get_file(_task_id(), args.package_id)
        if stored.kind != "package":
            raise DataError("ARTIFACT_NOT_FOUND", "文件不存在或无权访问")
        return json.dumps(
            {
                "ok": True,
                "package_id": stored.file_id,
                "file_name": stored.file_name,
                "file_size": stored.file_size,
                "sha256": stored.sha256,
                "files": stored.manifest,
                "expires_at": stored.expires_at,
                "untrusted": True,
            },
            ensure_ascii=False,
        )
    except DataError as exc:
        return _failure(exc)


async def upload_to_oss(args: OssFileArgs) -> str:
    try:
        stored, content = _owned_file(args.file_id)
        urls = await _oss().upload_files([(stored.file_name, content)], _oss_path())
        return _oss_result([stored], urls)
    except (DataError, OssUploadError) as exc:
        return _failure(exc)


async def upload_files_to_oss(args: OssFilesArgs) -> str:
    try:
        if len(args.file_ids) != len(set(args.file_ids)):
            raise DataError("OSS_UPLOAD_FAILED", "文件重复")
        owned = [_owned_file(file_id) for file_id in args.file_ids]
        urls = await _oss().upload_files([(item.file_name, content) for item, content in owned], _oss_path())
        return _oss_result([item for item, _content in owned], urls)
    except (DataError, OssUploadError) as exc:
        return _failure(exc)


def get_download_url(args: DownloadArgs) -> str:
    try:
        grant = _store().issue_download(_task_id(), args.file_id)
        return json.dumps(
            {
                "ok": True,
                "file_id": args.file_id,
                "file_name": grant.file_name,
                "file_size": grant.file_size,
                "sha256": grant.sha256,
                "download_path": grant.download_path,
                "expires_at": grant.expires_at,
                "untrusted": True,
            },
            ensure_ascii=False,
        )
    except DataError as exc:
        return _failure(exc)


def _load_text(args: ImportArgs) -> str:
    if (args.content is None) == (args.workspace_path is None):
        raise DataError("INVALID_TRANSFORM_RULE", "只能提供一段数据或一个工作区路径")
    if args.content is not None:
        return args.content
    settings = CURRENT_SETTINGS.get()
    try:
        root = workspace_root(settings)
        path = resolve_inside(root, args.workspace_path or "")
        if not path.is_file():
            raise DataError("DATASET_ACCESS_DENIED", "数据集不存在或无权访问")
        data = path.read_bytes()
    except FileDenied as exc:
        raise DataError("DATASET_ACCESS_DENIED", "数据集不存在或无权访问") from exc
    except OSError as exc:
        raise DataError("DATASET_ACCESS_DENIED", "数据集不存在或无权访问") from exc
    limit = 1_000_000 if settings is None else settings.data_max_bytes
    if len(data) > limit:
        raise DataError("FILE_TOO_LARGE", "输入文件超出大小上限")
    if b"\x00" in data:
        raise DataError("UNSUPPORTED_FILE_FORMAT", "不接受二进制输入")
    return data.decode("utf-8")


def _parse(text: str, args: ImportArgs) -> tuple[list[str], list[dict[str, Any]]]:
    settings = CURRENT_SETTINGS.get()
    max_rows = 5000 if settings is None else settings.data_max_rows
    max_columns = 64 if settings is None else settings.data_max_columns
    if args.data_format == "json":
        return parse_json_table(text, max_rows=max_rows, max_columns=max_columns)
    return parse_csv_table(text, max_rows=max_rows, max_columns=max_columns, delimiter=args.delimiter)


def _dataset_result(dataset_id: str, columns: list[str], rows: list[dict[str, Any]], summary: str) -> str:
    return json.dumps(
        {
            "ok": True,
            "dataset_id": dataset_id,
            "row_count": len(rows),
            "columns": columns,
            "sample": rows[: _sample_limit()],
            "summary": summary,
            "untrusted": True,
        },
        ensure_ascii=False,
    )


def _export(dataset_id: str, file_name: str, suffix: str, media: str, source_tool: str, render) -> str:
    try:
        safe = _file_name(file_name, suffix)
        columns, rows = _store().load_dataset(_task_id(), dataset_id)
        content = render(columns, rows)
        stored = _store().save_bytes(_task_id(), file_name=safe, media_type=media, content=content)
        return _file_result(stored, f"已导出 {len(rows)} 行", source_tool)
    except DataError as exc:
        return _failure(exc)


def _file_result(stored: StoredFile, summary: str, source_tool: str = "create_package") -> str:
    public = {
        "file_id": stored.file_id,
        "file_name": stored.file_name,
        "media_type": stored.media_type,
        "file_size": stored.file_size,
        "sha256": stored.sha256,
        "expires_at": stored.expires_at,
    }
    return json.dumps(
        {
            "ok": True,
            "summary": summary,
            **public,
            "artifacts": [
                {
                    "type": "package" if stored.kind == "package" else "file",
                    "title": stored.file_name,
                    "content": json.dumps(public, ensure_ascii=False),
                    "content_type": "application/vnd.qs.file+json",
                    "source_tool": source_tool,
                }
            ],
            "untrusted": True,
        },
        ensure_ascii=False,
    )


def _failure(exc: DataError | OssUploadError) -> str:
    return json.dumps(
        {"ok": False, "error": {"code": exc.code, "message": exc.message}, "untrusted": True},
        ensure_ascii=False,
    )


def _oss():
    client = CURRENT_OSS_CLIENT.get()
    if client is None:
        raise OssUploadError("OSS_UNAVAILABLE", "上传服务不可用")
    return client


def _oss_path() -> str:
    return _oss().upload_path_for(_task_id())


def _owned_file(file_id: str):
    stored = _store().get_file(_task_id(), file_id)
    return stored, _store().file_path(_task_id(), file_id).read_bytes()


def _oss_result(stored_files, urls: list[str]) -> str:
    files = []
    for stored, url in zip(stored_files, urls, strict=True):
        files.append(
            {
                "file_id": stored.file_id,
                "file_name": stored.file_name,
                "file_size": stored.file_size,
                "sha256": stored.sha256,
                "download_url": url,
            }
        )
    return json.dumps(
        {"ok": True, "files": files, "summary": f"已上传 {len(files)} 个文件", "untrusted": True},
        ensure_ascii=False,
    )


def _store() -> DataStore:
    store = CURRENT_DATA_STORE.get()
    if store is None:
        raise DataError("STORAGE_UNAVAILABLE", "数据存储不可用")
    return store


def _task_id() -> str:
    task_id = CURRENT_TASK_ID.get()
    if not task_id:
        raise DataError("STORAGE_UNAVAILABLE", "数据存储不可用")
    return task_id


def _file_name(name: str, suffix: str) -> str:
    if not _NAME.fullmatch(name) or not name.lower().endswith(suffix) or ".." in name:
        raise DataError("EXPORT_FAILED", "文件名不合法")
    return name


def _zip_path(path: str) -> str:
    if "\\" in path or path.startswith("/") or ":" in path:
        raise DataError("PACKAGE_BUILD_FAILED", "压缩包路径不合法")
    parts = path.split("/")
    if not parts or any(part in {"", ".", ".."} or _ZIP_PART.fullmatch(part) is None for part in parts):
        raise DataError("PACKAGE_BUILD_FAILED", "压缩包路径不合法")
    return "/".join(parts)


def _sheet_name(name: str) -> str:
    if any(char in name for char in "[]:*?/\\"):
        raise DataError("EXPORT_FAILED", "工作表名称不合法")
    return name


def _json_bytes(rows: list[dict[str, Any]]) -> bytes:
    safe = [{key: sanitize_cell(value) for key, value in row.items()} for row in rows]
    return json.dumps(safe, ensure_ascii=False, indent=2).encode("utf-8")


def _csv_value(value: Any) -> Any:
    sanitized = sanitize_cell(value)
    return "" if sanitized is None else sanitized


def _sample_limit() -> int:
    settings = CURRENT_SETTINGS.get()
    return 3 if settings is None else settings.data_sample_rows


def register_data_tools(registry) -> None:
    from app.tools.registry import ToolPricing, ToolSpec 

    specs = (
        ("import_dataset", ImportArgs, import_dataset),
        ("clean_data", CleanArgs, clean_data),
        ("deduplicate_data", DedupeArgs, deduplicate_data),
        ("normalize_data", NormalizeArgs, normalize_data),
        ("classify_data", ClassifyArgs, classify_data),
        ("validate_data", ValidateDataArgs, validate_data),
        ("export_json", ExportArgs, export_json),
        ("export_csv", ExportCsvArgs, export_csv),
        ("export_excel", ExportExcelArgs, export_excel),
        ("export_markdown", ExportMarkdownArgs, export_markdown),
        ("create_package", PackageArgs, create_package),
        ("list_package_files", PackageQuery, list_package_files),
        ("get_download_url", DownloadArgs, get_download_url),
    )
    for name, model, handler in specs:
        registry.register(
            ToolSpec(
                name=name,
                args_model=model,
                handler=handler,
                access="write_data",
                toolset="data",
                max_calls_per_run=8,
                timeout_s=20,
                pricing=ToolPricing(model="fixed_tokens", fixed_tokens=2000),  # ← 新增
            )
        )
    for name, model, handler in (
        ("upload_to_oss", OssFileArgs, upload_to_oss),
        ("upload_files_to_oss", OssFilesArgs, upload_files_to_oss),
    ):
        registry.register(
            ToolSpec(
                name=name,
                args_model=model,
                handler=handler,
                access="write_remote",
                requires_network=True,
                toolset="data",
                max_calls_per_run=8,
                timeout_s=20,
            )
        )


def _md(value: Any) -> str:
    sanitized = sanitize_cell(value)
    text = "" if sanitized is None else str(sanitized)
    return text.replace("|", "\\|").replace("\n", " ")
