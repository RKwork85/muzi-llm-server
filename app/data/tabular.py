"""有界的表格数据。值只允许标量，不执行公式。"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from typing import Any

_COLUMN = re.compile(r"^[\w\u4e00-\u9fff][\w\u4e00-\u9fff ._-]{0,63}$", re.UNICODE)
_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


class DataError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def parse_json_table(text: str, *, max_rows: int, max_columns: int) -> tuple[list[str], list[dict[str, Any]]]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DataError("UNSUPPORTED_FILE_FORMAT", "JSON 无法解析") from exc
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        payload = payload["records"]
    if not isinstance(payload, list):
        raise DataError("UNSUPPORTED_FILE_FORMAT", "JSON 需要是对象数组")
    return _rows_from_objects(payload, max_rows=max_rows, max_columns=max_columns)


def parse_csv_table(
    text: str,
    *,
    max_rows: int,
    max_columns: int,
    delimiter: str = ",",
) -> tuple[list[str], list[dict[str, Any]]]:
    if delimiter not in {",", ";", "\t"}:
        raise DataError("INVALID_TRANSFORM_RULE", "分隔符不在允许列表中")
    try:
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        raw_columns = list(reader.fieldnames or [])
        columns = [_column_name(name, index) for index, name in enumerate(raw_columns, start=1)]
        if len(columns) > max_columns:
            raise DataError("ROW_LIMIT_EXCEEDED", "列数超出上限")
        rows: list[dict[str, Any]] = []
        for index, item in enumerate(reader, start=1):
            if index > max_rows:
                raise DataError("ROW_LIMIT_EXCEEDED", "行数超出上限")
            row = {}
            for source, column in zip(raw_columns, columns, strict=True):
                row[column] = "" if item.get(source) is None else str(item.get(source))
            rows.append(row)
    except csv.Error as exc:
        raise DataError("UNSUPPORTED_FILE_FORMAT", "CSV 无法解析") from exc
    if len(set(columns)) != len(columns):
        raise DataError("INVALID_COLUMN", "列名重复")
    return columns, rows


def clean_rows(
    columns: list[str],
    rows: list[dict[str, Any]],
    *,
    drop_empty_rows: bool,
    trim_strings: bool,
    drop_if_blank: list[str],
) -> tuple[list[dict[str, Any]], int]:
    _require_columns(columns, drop_if_blank)
    kept: list[dict[str, Any]] = []
    removed = 0
    for row in rows:
        current = dict(row)
        if trim_strings:
            for key, value in list(current.items()):
                if isinstance(value, str):
                    current[key] = value.strip()
        blank = all(_empty(current.get(column)) for column in columns) if columns else not current
        targeted = any(_empty(current.get(column)) for column in drop_if_blank)
        if (drop_empty_rows and blank) or targeted:
            removed += 1
            continue
        kept.append(current)
    return kept, removed


def deduplicate_rows(
    columns: list[str],
    rows: list[dict[str, Any]],
    keys: list[str],
) -> tuple[list[dict[str, Any]], int]:
    selected = keys or columns
    _require_columns(columns, selected)
    seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    removed = 0
    for row in rows:
        marker = json.dumps([row.get(key) for key in selected], ensure_ascii=False, sort_keys=True)
        if marker in seen:
            removed += 1
            continue
        seen.add(marker)
        kept.append(row)
    return kept, removed


def normalize_rows(
    columns: list[str],
    rows: list[dict[str, Any]],
    *,
    rename: dict[str, str],
    strip_columns: list[str],
    date_columns: list[str],
    number_columns: list[str],
) -> tuple[list[str], list[dict[str, Any]]]:
    _require_columns(columns, list(rename) + strip_columns + date_columns + number_columns)
    new_columns: list[str] = []
    for column in columns:
        target = rename.get(column, column)
        if not _COLUMN.fullmatch(target):
            raise DataError("INVALID_COLUMN", "标准化后的列名不合法")
        new_columns.append(target)
    if len(set(new_columns)) != len(new_columns):
        raise DataError("INVALID_COLUMN", "标准化后列名重复")
    mapping = dict(zip(columns, new_columns, strict=True))
    output: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {}
        for source, target in mapping.items():
            value = row.get(source)
            if source in strip_columns and isinstance(value, str):
                value = value.strip()
            if source in date_columns:
                value = _date(value)
            if source in number_columns:
                value = _number(value)
            item[target] = value
        output.append(item)
    return new_columns, output


def classify_rows(
    columns: list[str],
    rows: list[dict[str, Any]],
    *,
    column: str,
    rules: list[dict[str, str]],
    default_label: str,
    output_column: str,
) -> tuple[list[str], list[dict[str, Any]], dict[str, int]]:
    _require_columns(columns, [column])
    if not _COLUMN.fullmatch(output_column):
        raise DataError("INVALID_COLUMN", "分类列名不合法")
    if output_column in columns:
        raise DataError("INVALID_COLUMN", "分类列已存在")
    if not rules:
        raise DataError("INVALID_TRANSFORM_RULE", "分类规则不能为空")
    counts: dict[str, int] = {}
    output: list[dict[str, Any]] = []
    for row in rows:
        value = "" if row.get(column) is None else str(row.get(column))
        label = default_label
        for rule in rules:
            label_value = rule.get("label")
            if not isinstance(label_value, str) or not label_value.strip():
                raise DataError("INVALID_TRANSFORM_RULE", "分类规则缺少标签")
            equals = rule.get("equals")
            contains = rule.get("contains")
            if equals is not None and value == equals:
                label = rule["label"]
                break
            if contains is not None and contains in value:
                label = rule["label"]
                break
        item = dict(row)
        item[output_column] = label
        output.append(item)
        counts[label] = counts.get(label, 0) + 1
    return [*columns, output_column], output, counts


def validate_rows(
    columns: list[str],
    rows: list[dict[str, Any]],
    *,
    required: list[str],
    types: dict[str, str],
    enums: dict[str, list[str]],
) -> list[dict[str, object]]:
    _require_columns(columns, required + list(types) + list(enums))
    issues: list[dict[str, object]] = []
    for index, row in enumerate(rows, start=1):
        for column in required:
            if _empty(row.get(column)):
                issues.append({"row": index, "column": column, "problem": "required"})
        for column, expected in types.items():
            if _empty(row.get(column)):
                continue
            if not _matches_type(row.get(column), expected):
                issues.append({"row": index, "column": column, "problem": "type"})
        for column, allowed in enums.items():
            value = row.get(column)
            if _empty(value):
                continue
            if str(value) not in allowed:
                issues.append({"row": index, "column": column, "problem": "enum"})
        if len(issues) >= 20:
            break
    return issues


def sanitize_cell(value: Any) -> Any:
    if isinstance(value, bool) or isinstance(value, int | float) or value is None:
        return value
    text = str(value)
    if text.startswith(_DANGEROUS):
        return "'" + text
    return text


def _rows_from_objects(items: list[Any], *, max_rows: int, max_columns: int) -> tuple[list[str], list[dict[str, Any]]]:
    if len(items) > max_rows:
        raise DataError("ROW_LIMIT_EXCEEDED", "行数超出上限")
    columns: list[str] = []
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise DataError("UNSUPPORTED_FILE_FORMAT", "每一行必须是对象")
        row: dict[str, Any] = {}
        for key, value in item.items():
            name = _column_name(str(key), len(columns) + 1)
            if name not in columns:
                columns.append(name)
                if len(columns) > max_columns:
                    raise DataError("ROW_LIMIT_EXCEEDED", "列数超出上限")
            row[name] = _scalar(value)
        rows.append(row)
    for row in rows:
        for column in columns:
            row.setdefault(column, None)
    return columns, rows


def _column_name(name: str, index: int) -> str:
    cleaned = name.strip()
    if not cleaned or not _COLUMN.fullmatch(cleaned):
        raise DataError("INVALID_COLUMN", f"第 {index} 列名称不合法")
    return cleaned


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        if isinstance(value, float) and value != value:
            raise DataError("UNSUPPORTED_FILE_FORMAT", "不接受 NaN")
        return value
    raise DataError("UNSUPPORTED_FILE_FORMAT", "不接受嵌套对象或数组")


def _require_columns(columns: list[str], requested: list[str]) -> None:
    missing = [item for item in requested if item not in columns]
    if missing:
        raise DataError("INVALID_COLUMN", "列不存在")


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _date(value: Any) -> Any:
    if _empty(value):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise DataError("INVALID_TRANSFORM_RULE", "日期无法按 ISO 格式解析") from exc
    return parsed.date().isoformat()


def _number(value: Any) -> Any:
    if _empty(value):
        return None
    if isinstance(value, bool):
        raise DataError("INVALID_TRANSFORM_RULE", "布尔值不能当作数值")
    if isinstance(value, int | float):
        return value
    text = str(value).strip().replace(",", "")
    try:
        number = float(text) if "." in text else int(text)
    except ValueError as exc:
        raise DataError("INVALID_TRANSFORM_RULE", "数值无法解析") from exc
    return number


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    raise DataError("INVALID_TRANSFORM_RULE", "类型约束不在允许列表中")
