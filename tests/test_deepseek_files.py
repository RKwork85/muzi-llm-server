"""DeepSeek 文件上传的解析方式自动判断。"""

from __future__ import annotations

import io
import zipfile

from app.api.v1.endpoints.deepseek import _auto_purpose, _document_has_images


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_image_always_has_images() -> None:
    assert _document_has_images("scan.bmp", b"bm") is True
    assert _auto_purpose("scan.tiff", b"") == "rich_layout_recognition"


def test_plain_docx_without_media_uses_text() -> None:
    content = _zip({"word/document.xml": b"<w:document/>"})
    assert _document_has_images("report.docx", content) is False
    assert _auto_purpose("report.docx", content) == "text_recognition"


def test_docx_with_media_uses_rich() -> None:
    content = _zip(
        {
            "word/document.xml": b"<w:document/>",
            "word/media/image1.png": b"\x89PNG",
        }
    )
    assert _auto_purpose("report.docx", content) == "rich_layout_recognition"


def test_pptx_media_uses_rich() -> None:
    content = _zip({"ppt/media/image1.png": b"\x89PNG"})
    assert _auto_purpose("slides.pptx", content) == "rich_layout_recognition"


def test_legacy_doc_conservative_rich() -> None:
    assert _auto_purpose("legacy.doc", b"\xd0\xcf") == "rich_layout_recognition"


def test_pdf_without_images_uses_text(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.v1.endpoints.deepseek._pdf_has_images", lambda content: False
    )
    assert _auto_purpose("plain.pdf", b"%PDF") == "text_recognition"


def test_pdf_with_images_uses_rich(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.v1.endpoints.deepseek._pdf_has_images", lambda content: True
    )
    assert _auto_purpose("chart.pdf", b"%PDF") == "rich_layout_recognition"
