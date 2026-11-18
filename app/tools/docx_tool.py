"""把结构化块写成真正的 .docx。基于 python-docx，支持标题/段落/列表/表格/图片/分页。

为什么不用手写 OOXML：手写版本只能表达 paragraph，表格要自己算列宽和单元格 XML，
图片要额外维护 rels 与 drawing，横向页面还要交换页面尺寸。python-docx 已经把这些
封好了，服务端进程里本来就装了它（要求见 requirements.txt）。
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.tools.file_sandbox import FileDenied, display_path, resolve_inside
from app.tools.file_tools import workspace_root
from app.tools.web_tools import CURRENT_SETTINGS

_MAX_BLOCKS = 80
_MAX_TEXT = 4000
_MAX_TOTAL = 80_000
_MAX_TABLE_ROWS = 60
_MAX_TABLE_COLS = 12
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_HEADER_FILL = "1F3864"
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff"}
_TEXT_KINDS = {"heading", "paragraph", "bullet"}


class _DocxError(Exception):
    """块级校验失败：带自己的错误码，避免和「路径不可用」混在一起。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DocBlock(BaseModel):
    """一个文档块。kind 决定用到哪些字段，未用到的字段保持默认即可。

    table 用 headers + rows；image 用 path（工作区内相对路径）+ 可选 width_mm/height_mm；
    其余 kind 只用 text（heading 额外用 level）。
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["heading", "paragraph", "bullet", "table", "image", "page_break"] = "paragraph"
    text: str = Field(default="", max_length=_MAX_TEXT)
    level: int = Field(default=1, ge=1, le=3)
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    caption: str = Field(default="", max_length=400)
    path: str = Field(default="", max_length=240)
    width_mm: float | None = Field(default=None, gt=0, le=500)
    height_mm: float | None = Field(default=None, gt=0, le=500)

    @model_validator(mode="after")
    def _validate_by_kind(self) -> "DocBlock":
        if self.kind in _TEXT_KINDS:
            if not self.text.strip():
                raise ValueError(f"{self.kind} 需要非空 text")
            return self
        if self.kind == "table":
            if not self.headers:
                raise ValueError("table 需要 headers")
            if not self.rows:
                raise ValueError("table 需要 rows")
            if len(self.headers) > _MAX_TABLE_COLS:
                raise ValueError(f"表格列数超过 {_MAX_TABLE_COLS}")
            if len(self.rows) > _MAX_TABLE_ROWS:
                raise ValueError(f"表格行数超过 {_MAX_TABLE_ROWS}")
            width = len(self.headers)
            for row in self.rows:
                if len(row) != width:
                    raise ValueError("rows 每行的列数必须等于 headers 的列数")
                for cell in row:
                    if len(cell) > _MAX_TEXT:
                        raise ValueError("表格单元格文本超出长度上限")
            if any(len(cell) > _MAX_TEXT for cell in self.headers):
                raise ValueError("表头文本超出长度上限")
            return self
        if self.kind == "image":
            if not self.path.strip():
                raise ValueError("image 需要 path")
            if Path(self.path).suffix.lower() not in _IMAGE_SUFFIXES:
                raise ValueError("图片必须是 png/jpg/jpeg/gif/bmp/tif/tiff")
        return self


class WriteDocxArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=240)
    title: str = Field(default="", max_length=120)
    orientation: Literal["portrait", "landscape"] = "portrait"
    blocks: list[DocBlock] = Field(min_length=1, max_length=_MAX_BLOCKS)


def write_docx(args: WriteDocxArgs) -> str:
    try:
        if not args.path.lower().endswith(".docx"):
            return _error("file_unavailable", "文件名必须以 .docx 结尾")
        total = len(args.title) + sum(_block_chars(block) for block in args.blocks)
        if total > _MAX_TOTAL:
            return _error("file_unavailable", "文档超出长度上限")
        root = workspace_root(CURRENT_SETTINGS.get())
        target = resolve_inside(root, args.path)
        if target == root or target.is_dir():
            raise FileDenied()
        # 先把所有图片读进内存再写文件：避免写到一半才发现图片缺失，留下半个 docx。
        data = build_docx(
            args.title,
            args.blocks,
            root=root,
            orientation=args.orientation,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        target.write_bytes(data)
        return json.dumps(
            {
                "source": display_path(root, target),
                "file_name": target.name,
                "bytes": len(data),
                "created": not existed,
                "summary": "已生成 Word 文档。请调用 deliver_file 取得文件位置。回答里只在最末尾写一次：另起一行「附件：文件名」，并把 download_url 作为 Markdown 链接原样接在后面。",
            },
            ensure_ascii=False,
        )
    except FileDenied:
        return _error("file_unavailable", "路径不可用")
    except _DocxError as exc:
        return _error(exc.code, exc.message)
    except OSError:
        return _error("file_unavailable", "路径不可用")


def _block_chars(block: DocBlock) -> int:
    chars = len(block.text) + len(block.caption)
    chars += sum(len(item) for item in block.headers)
    chars += sum(len(cell) for row in block.rows for cell in row)
    return chars


def build_docx(
    title: str,
    blocks: list[DocBlock],
    *,
    root: Path | None = None,
    orientation: str = "portrait",
) -> bytes:
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.shared import Mm, Pt

    document = Document()
    _setup_page(document, orientation, WD_ORIENT, Mm)
    _setup_fonts(document, Pt)

    heading = title.strip()
    if heading:
        document.add_heading(heading, level=1)

    for block in blocks:
        _append_block(document, block, root, Mm)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _setup_page(document, orientation: str, wd_orient, mm) -> None:
    section = document.sections[0]
    if orientation == "landscape":
        section.orientation = wd_orient.LANDSCAPE
        section.page_width, section.page_height = mm(297), mm(210)
    else:
        section.orientation = wd_orient.PORTRAIT
        section.page_width, section.page_height = mm(210), mm(297)
    section.left_margin = section.right_margin = mm(20)
    section.top_margin = section.bottom_margin = mm(18)


def _setup_fonts(document, pt) -> None:
    """中文字体：正文和标题都用微软雅黑，并显式写 w:eastAsia，否则中文会 fallback。"""
    from docx.oxml.ns import qn

    for name in ("Normal", "Heading 1", "Heading 2", "Heading 3"):
        try:
            style = document.styles[name]
        except KeyError:
            continue
        style.font.name = "Microsoft YaHei"
        rpr = style.element.get_or_add_rPr()
        fonts = rpr.get_or_add_rFonts()
        fonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    document.styles["Normal"].font.size = pt(10.5)


def _append_block(document, block: DocBlock, root: Path | None, mm) -> None:
    if block.kind == "heading":
        document.add_heading(block.text, level=block.level)
        return
    if block.kind == "bullet":
        document.add_paragraph(block.text, style="List Bullet")
        return
    if block.kind == "paragraph":
        document.add_paragraph(block.text)
        return
    if block.kind == "page_break":
        document.add_page_break()
        return
    if block.kind == "table":
        _append_table(document, block)
        return
    if block.kind == "image":
        _append_image(document, block, root, mm)
        return
    raise _DocxError("file_unavailable", f"不支持的块类型: {block.kind}")


def _append_table(document, block: DocBlock) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    columns = len(block.headers)
    table = document.add_table(rows=1 + len(block.rows), cols=columns)
    table.style = "Table Grid"
    table.autofit = True

    header_cells = table.rows[0].cells
    for index, text in enumerate(block.headers):
        cell = header_cells[index]
        cell.text = ""
        paragraph = cell.paragraphs[0]
        run = paragraph.add_run(text)
        run.bold = True
        run.font.size = Pt(10)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        _shade(cell, _HEADER_FILL, OxmlElement, qn)

    for row_index, values in enumerate(block.rows, start=1):
        cells = table.rows[row_index].cells
        for column, value in enumerate(values):
            cells[column].text = value

    if block.caption.strip():
        caption = document.add_paragraph()
        run = caption.add_run(block.caption)
        run.italic = True
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(0x59, 0x59, 0x59)


def _shade(cell, fill: str, oxml_element, qn) -> None:
    properties = cell._tc.get_or_add_tcPr()
    shading = oxml_element("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), fill)
    properties.append(shading)


def _append_image(document, block: DocBlock, root: Path | None, mm) -> None:
    if root is None:
        raise _DocxError("file_unavailable", "图片路径不可用")
    try:
        source = resolve_inside(root, block.path)
    except FileDenied:
        raise _DocxError("file_unavailable", f"图片路径不可用: {block.path}") from None
    if not source.is_file():
        raise _DocxError("image_missing", f"图片不存在: {block.path}")
    try:
        size = source.stat().st_size
    except OSError:
        raise _DocxError("file_unavailable", f"图片不可读: {block.path}") from None
    if size > _MAX_IMAGE_BYTES:
        raise _DocxError("image_too_large", "图片超出 8MB 上限")
    try:
        payload = source.read_bytes()
    except OSError:
        raise _DocxError("file_unavailable", f"图片不可读: {block.path}") from None

    kwargs: dict[str, object] = {}
    if block.width_mm:
        kwargs["width"] = mm(block.width_mm)
    if block.height_mm:
        kwargs["height"] = mm(block.height_mm)
    if not kwargs:
        kwargs["width"] = mm(150)
    try:
        document.add_picture(io.BytesIO(payload), **kwargs)
    except Exception:  # noqa: BLE001 - python-docx 对损坏图片抛多种异常
        raise _DocxError("image_unsupported", f"图片无法解析: {block.path}") from None


def _error(code: str, message: str) -> str:
    return json.dumps(
        {"source": "workspace", "error": {"code": code, "message": message}},
        ensure_ascii=False,
    )
