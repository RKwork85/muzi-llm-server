"""最小 XLSX 写入。不加载宏，也不读取外部资源。"""

from __future__ import annotations

import html
import io
import zipfile
from typing import Any


def build_xlsx(sheet_name: str, headers: list[str], rows: list[list[Any]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", _CONTENT_TYPES)
        package.writestr("_rels/.rels", _ROOT_RELS)
        package.writestr("xl/workbook.xml", _workbook(sheet_name))
        package.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS)
        package.writestr("xl/worksheets/sheet1.xml", _sheet(headers, rows))
    return buffer.getvalue()


def _sheet(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>',
    ]
    all_rows = [headers, *rows]
    for row_index, row in enumerate(all_rows, start=1):
        cells = []
        for column_index, value in enumerate(row, start=1):
            ref = f"{_column_name(column_index)}{row_index}"
            cells.append(_cell(ref, value, header=row_index == 1))
        lines.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    lines.append("</sheetData></worksheet>")
    return "".join(lines)


def _cell(ref: str, value: Any, *, header: bool) -> str:
    if not header and isinstance(value, bool):
        return _text(ref, "true" if value else "false")
    if not header and isinstance(value, int | float) and not isinstance(value, bool):
        return f'<c r="{ref}"><v>{value}</v></c>'
    if value is None:
        return _text(ref, "")
    return _text(ref, str(value))


def _text(ref: str, value: str) -> str:
    escaped = html.escape(value, quote=False)
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escaped}</t></is></c>'


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _workbook(sheet_name: str) -> str:
    safe = html.escape(sheet_name, quote=True)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{safe}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )


_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
