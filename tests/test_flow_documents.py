"""画布文档：key 校验，以及 data URL 转 OSS 地址。"""

from __future__ import annotations

import base64

import pytest

from app.core.exceptions import InvalidRequestError
from app.services.flow_service import externalize_data_urls, parse_doc_key


def test_parse_doc_key_accepts_canvas_parts() -> None:
    assert parse_doc_key("canvas:meidiacreat:my_app:flw_1") == "canvas"
    assert parse_doc_key("sessions:meidiacreat:text2image") == "sessions"
    assert parse_doc_key("apps:meidiacreat") == "apps"


def test_parse_doc_key_rejects_path_tricks() -> None:
    with pytest.raises(InvalidRequestError):
        parse_doc_key("../canvas:meidiacreat")
    with pytest.raises(InvalidRequestError):
        parse_doc_key("canvas:meidiacreat/secret")


class _FakeOss:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def upload_files(self, files, upload_path="", **kwargs):
        self.calls.append({"files": files, **kwargs})
        return ["https://oss.muzihhh.com/files/users/u/image/2026-11/ab12_flow_1.png"]


@pytest.mark.asyncio
async def test_data_url_is_replaced_with_oss_url() -> None:
    raw = base64.b64encode(b"png-bytes").decode()
    payload = {"nodes": [{"data": {"src": f"data:image/png;base64,{raw}"}}]}
    oss = _FakeOss()
    await externalize_data_urls(payload, oss, "803445db-1e06-4be6-ba50-99f2e842cf50", "flw_1")
    assert payload["nodes"][0]["data"]["src"].startswith("https://oss.muzihhh.com/files/")
    assert oss.calls[0]["category"] == "image"
    assert oss.calls[0]["files"][0][1] == b"png-bytes"


@pytest.mark.asyncio
async def test_data_url_is_dropped_without_oss() -> None:
    raw = base64.b64encode(b"png-bytes").decode()
    payload = {"src": f"data:image/png;base64,{raw}"}
    await externalize_data_urls(payload, None, "803445db-1e06-4be6-ba50-99f2e842cf50", "flw_1")
    assert payload["src"] == ""
