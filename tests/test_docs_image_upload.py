"""正文插图上传：文件头识别与路由注册。纯函数 + OpenAPI，不依赖数据库。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.v1.endpoints.docs import _detect_image


def test_detect_image_by_signature() -> None:
    assert _detect_image(b"\x89PNG\r\n\x1a\n" + b"\0" * 8) == (".png", "image/png")
    assert _detect_image(b"\xff\xd8\xff" + b"\0" * 8) == (".jpg", "image/jpeg")
    assert _detect_image(b"GIF89a" + b"\0" * 8) == (".gif", "image/gif")
    # WebP 的识别看 RIFF 容器里的 WEBP 标记，不能只认前四个字节
    webp = b"RIFF" + b"\0\0\0\0" + b"WEBP" + b"\0" * 8
    assert _detect_image(webp) == (".webp", "image/webp")


def test_detect_image_rejects_non_image() -> None:
    assert _detect_image(b"hello world") is None
    assert _detect_image(b"") is None
    # RIFF 但不是 WEBP（例如 WAV）不能被当成图片
    assert _detect_image(b"RIFF\0\0\0\0WAVE" + b"\0" * 8) is None


def test_doc_image_route_is_registered(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/docs/images" in paths
    assert "post" in paths["/api/v1/docs/images"]
