"""把结构化段落写成真正的 .docx。不加载宏，也不引用外部资源。"""

from __future__ import annotations

import html
import io
import json
import zipfile
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.tools.file_sandbox import FileDenied, display_path, resolve_inside
from app.tools.file_tools import workspace_root
from app.tools.web_tools import CURRENT_SETTINGS

_MAX_BLOCKS = 80
_MAX_TEXT = 4000
_MAX_TOTAL = 80_000


class DocBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["heading", "paragraph", "bullet"]
    text: str = Field(min_length=1, max_length=_MAX_TEXT)
    level: int = Field(default=1, ge=1, le=3)


class WriteDocxArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=240)
    title: str = Field(default="", max_length=120)
    blocks: list[DocBlock] = Field(min_length=1, max_length=_MAX_BLOCKS)


def write_docx(args: WriteDocxArgs) -> str:
    try:
        if not args.path.lower().endswith(".docx"):
            return _error("file_unavailable", "文件名必须以 .docx 结尾")
        total = len(args.title) + sum(len(block.text) for block in args.blocks)
        if total > _MAX_TOTAL:
            return _error("file_unavailable", "文档超出长度上限")
        root = workspace_root(CURRENT_SETTINGS.get())
        target = resolve_inside(root, args.path)
        if target == root or target.is_dir():
            raise FileDenied()
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        data = build_docx(args.title, args.blocks)
        target.write_bytes(data)
        return json.dumps(
            {
                "source": display_path(root, target),
                "file_name": target.name,
                "bytes": len(data),
                "created": not existed,
                "summary": "已生成 Word 文档。请调用 deliver_file，并把 download_url 原样发给用户。",
            },
            ensure_ascii=False,
        )
    except FileDenied:
        return _error("file_unavailable", "路径不可用")
    except OSError:
        return _error("file_unavailable", "路径不可用")


def build_docx(title: str, blocks: list[DocBlock]) -> bytes:
    paragraphs: list[str] = []
    heading = title.strip()
    if heading:
        paragraphs.append(_paragraph("heading", heading, 1))
    for block in blocks:
        paragraphs.append(_paragraph(block.kind, block.text, block.level))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(paragraphs)}{_SECT}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", _CONTENT_TYPES)
        package.writestr("_rels/.rels", _ROOT_RELS)
        package.writestr("word/document.xml", document)
        package.writestr("word/styles.xml", _STYLES)
        package.writestr("word/numbering.xml", _NUMBERING)
        package.writestr("word/_rels/document.xml.rels", _DOCUMENT_RELS)
    return buffer.getvalue()


def _paragraph(kind: str, text: str, level: int) -> str:
    props: list[str] = []
    if kind == "heading":
        props.append(f'<w:pStyle w:val="Heading{level}"/>')
    elif kind == "bullet":
        props.append('<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>')
    style = f"<w:pPr>{''.join(props)}</w:pPr>" if props else ""
    return f"<w:p>{style}{_runs(text)}</w:p>"


def _runs(text: str) -> str:
    pieces: list[str] = []
    for index, line in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n")):
        if index:
            pieces.append("<w:br/>")
        pieces.append(f'<w:t xml:space="preserve">{html.escape(line, quote=False)}</w:t>')
    return f"<w:r>{''.join(pieces)}</w:r>"


def _error(code: str, message: str) -> str:
    return json.dumps(
        {"source": "workspace", "error": {"code": code, "message": message}},
        ensure_ascii=False,
    )


_SECT = (
    "<w:sectPr>"
    '<w:pgSz w:w="11906" w:h="16838"/>'
    '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
    'w:header="720" w:footer="720" w:gutter="0"/>'
    "</w:sectPr>"
)

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_DOCUMENT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
</Relationships>"""

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:uiPriority w:val="9"/><w:qFormat/><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:uiPriority w:val="9"/><w:qFormat/><w:pPr><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:uiPriority w:val="9"/><w:qFormat/><w:pPr><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:b/><w:sz w:val="24"/></w:rPr></w:style>
</w:styles>"""

_NUMBERING = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="hybridMultilevel"/><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/><w:lvlJc w:val="left"/></w:lvl></w:abstractNum>
<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
</w:numbering>"""
