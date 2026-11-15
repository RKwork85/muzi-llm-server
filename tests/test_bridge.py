"""自建 MCP 桥接（企微/飞书）的多租户凭据与 MCP 协议测试。不访问外网。"""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.support_agent_access import allow_legacy_agent_api


@pytest.fixture(autouse=True)
def _legacy_agent_api(monkeypatch) -> None:
    allow_legacy_agent_api(monkeypatch)


from app.main import create_app
from app.mcp.catalog import Connector, load_catalog
from app.mcp.service import McpService
from app.mcp.store import MemoryStore
from app.bridge import feishu, wecom
from tests.conftest import make_settings
from tests.fakes import FakeProvider


def _settings(**overrides) -> object:
    return make_settings(
        mcp_token_key="test-token-key-012345",
        mcp_allow_insecure=True,
        mcp_public_base_url="http://app.test",
        mcp_frontend_base_url="http://front.test",
        mcp_timeout_seconds=5,
        **overrides,
    )


def _catalog() -> tuple[Connector, ...]:
    return (
        Connector("wecom", "企业微信", "streamable_http", "http://app.test/bridge/wecom", False, issuer="http://app.test/bridge/wecom"),
        Connector("feishu", "飞书", "streamable_http", "http://app.test/bridge/feishu", False, issuer="http://app.test/bridge/feishu"),
    )


_WECOM_CRED = {"corp_id": "corp-demo", "agent_id": "1000002", "secret": "secret-demo"}


def test_catalog_expands_env_and_filters_insecure() -> None:
    loaded = load_catalog(allow_insecure=False)
    # 未开启 allow_insecure 时，http 桥接连接器被过滤，只留 https 的 notion。
    assert all(item.url.startswith("https://") for item in loaded)


def test_bridge_well_known_endpoints() -> None:
    app = create_app(settings=_settings(), provider=FakeProvider())
    with TestClient(app, base_url="http://app.test") as client:
        protected = client.get("/.well-known/oauth-protected-resource")
        assert protected.status_code == 200
        servers = protected.json()["authorization_servers"]
        assert "http://app.test/bridge/wecom" in servers
        assert "http://app.test/bridge/feishu" in servers

        meta = client.get("/.well-known/oauth-authorization-server/bridge/wecom")
        assert meta.status_code == 200
        body = meta.json()
        assert body["issuer"] == "http://app.test/bridge/wecom"
        assert body["authorization_endpoint"].endswith("/bridge/wecom/authorize")
        assert body["token_endpoint"].endswith("/bridge/wecom/token")


def test_bridge_configure_credentials_and_discover() -> None:
    """保存用户凭据后签发连接，tools/list 能按用户凭据列出企微工具。"""
    settings = _settings()
    app = create_app(settings=settings, provider=FakeProvider())
    transport = httpx.ASGITransport(app=app)

    async def run() -> tuple[list[dict], list[str], list[str], object]:
        async with httpx.AsyncClient(transport=transport, base_url="http://app.test") as client:
            service = McpService(settings, MemoryStore(), client, _catalog(), {"token-a": "user-a"})
            await service.configure_bridge("user-a", "wecom", dict(_WECOM_CRED))
            connection = await service.store.get_connection("user-a", "wecom")
            assert connection is not None and connection.status == "connected"
            cred = await service.store.get_bridge_credential("user-a", "wecom")
            assert cred is not None and cred.credentials_encrypted
            tools = await service.discover("user-a", "wecom")
            names = [item["name"] for item in tools]
            chosen = await service.select("user-a", "wecom", names)
            specs = await service.tool_specs("user-a")
            return tools, chosen, [item.name for item in specs], cred

    tools, chosen, spec_names, cred = asyncio.run(run())
    assert {item["name"] for item in tools} == {"send_text", "list_departments"}
    assert set(chosen) == {"send_text", "list_departments"}
    assert "mcp__wecom__send_text" in spec_names
    # 密文不含明文凭据
    assert "corp-demo" not in cred.credentials_encrypted


def test_bridge_oauth_without_credentials_is_empty() -> None:
    """旧的「确认连接」不携带凭据，tools/list 返回空。"""
    settings = _settings()
    app = create_app(settings=settings, provider=FakeProvider())
    transport = httpx.ASGITransport(app=app)

    async def run() -> list[dict]:
        async with httpx.AsyncClient(transport=transport, base_url="http://app.test") as client:
            service = McpService(settings, MemoryStore(), client, _catalog(), {"token-a": "user-a"})
            url = await service.begin("user-a", "wecom")
            response = await client.get(url, follow_redirects=False)
            assert response.status_code == 302
            query = parse_qs(urlparse(response.headers["location"]).query)
            await service.finish(query["code"][0], query["state"][0])
            return await service.discover("user-a", "wecom")

    assert asyncio.run(run()) == []


def test_bridge_credentials_status_and_disconnect() -> None:
    """凭据状态查询与断开清理。"""
    settings = _settings()

    async def run() -> tuple[object, object]:
        service = McpService(settings, MemoryStore(), httpx.AsyncClient(), _catalog(), {"token-a": "user-a"})
        before = await service.bridge_credentials_status("user-a", "wecom")
        assert before["configured"] is False
        await service.configure_bridge("user-a", "wecom", dict(_WECOM_CRED))
        after = await service.bridge_credentials_status("user-a", "wecom")
        assert after["configured"] is True
        await service.disconnect("user-a", "wecom")
        gone = await service.bridge_credentials_status("user-a", "wecom")
        assert gone["configured"] is False
        return after, gone

    asyncio.run(run())


def test_bridge_wecom_send_text(monkeypatch) -> None:
    """tools/call 的企微发消息路径，替换真实 HTTP。"""
    settings = _settings()
    captured: dict[str, object] = {}

    class FakeResponse:
        def __init__(self, data: dict) -> None:
            self._data = data

        def json(self) -> dict:
            return self._data

    class FakeClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> bool:
            return False

        async def get(self, url, *, params=None):
            captured["get_url"] = url
            captured["get_params"] = params
            return FakeResponse({"errcode": 0, "errmsg": "ok", "access_token": "tok"})

        async def post(self, url, *, params=None, json=None):
            captured["post_url"] = url
            captured["post_params"] = params
            captured["post_json"] = json
            return FakeResponse({"errcode": 0, "errmsg": "ok", "msgid": "msg-1"})

    monkeypatch.setattr(wecom, "_cached", {})
    monkeypatch.setattr(wecom.httpx, "AsyncClient", FakeClient)

    result = asyncio.run(wecom.call_tool("user-a", dict(_WECOM_CRED), 5, "send_text", {"touser": "zhang|li", "content": "你好"}))
    assert result["isError"] is False
    assert "msg-1" in result["content"][0]["text"]
    assert captured["post_json"]["agentid"] == 1000002


_FEISHU_CRED = {"app_id": "a", "app_secret": "s"}


class _FakeFeishuResponse:
    def __init__(self, data: dict) -> None:
        self._data = data

    def json(self) -> dict:
        return self._data


class _FakeFeishuClient:
    """按 (method, url 子串) 匹配返回预设响应，其余返回 unexpected。"""

    def __init__(self, routes: list[tuple[str, str, dict]]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, dict]] = []

    async def __aenter__(self) -> "_FakeFeishuClient":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def request(self, method: str, url: str, **kwargs: object) -> _FakeFeishuResponse:
        self.calls.append((method, url, kwargs))
        for m, key, resp in self.routes:
            if method == m and key in url:
                return _FakeFeishuResponse(resp)
        return _FakeFeishuResponse({"code": -1, "msg": "unexpected"})


_TOKEN_ROUTE = ("POST", "tenant_access_token", {"code": 0, "msg": "ok", "tenant_access_token": "tok"})


def _feishu_call(monkeypatch, name: str, arguments: dict, routes: list[tuple[str, str, dict]]) -> tuple[dict, _FakeFeishuClient]:
    monkeypatch.setattr(feishu, "_cached", {})
    client = _FakeFeishuClient([_TOKEN_ROUTE, *routes])
    monkeypatch.setattr(feishu.httpx, "AsyncClient", lambda *a, **k: client)
    result = asyncio.run(feishu.call_tool("user-a", dict(_FEISHU_CRED), 5, name, arguments))
    return result, client


def test_bridge_feishu_create_doc(monkeypatch) -> None:
    """飞书创建云文档：标题行转 heading，正文保持原文。"""
    _settings()
    result, client = _feishu_call(
        monkeypatch,
        "create_doc",
        {"title": "标题", "content": "# 一\n正文"},
        [
            ("POST", "/docx/v1/documents", {"code": 0, "msg": "ok", "data": {"document": {"document_id": "doc-1"}}}),
            ("POST", "/children", {"code": 0, "msg": "ok"}),
        ],
    )
    assert result["isError"] is False
    assert "doc-1" in result["content"][0]["text"]
    children = client.calls[-1][2]["json"]["children"]
    assert children[0]["block_type"] == 3  # heading1
    assert children[1]["block_type"] == 2  # text
    assert children[0]["heading1"]["elements"][0]["text_run"]["content"] == "一"
    assert children[1]["text"]["elements"][0]["text_run"]["content"] == "正文"


def test_bridge_feishu_contact_tools(monkeypatch) -> None:
    """飞书通讯录：查用户、列部门、列部门成员。"""
    _settings()
    result, _ = _feishu_call(
        monkeypatch,
        "get_user",
        {"user_id": "ou_x", "user_id_type": "open_id"},
        [("GET", "/contact/v3/users/", {"code": 0, "msg": "ok", "data": {"user": {"name": "张三", "open_id": "ou_x"}}})],
    )
    assert result["isError"] is False
    assert "张三" in result["content"][0]["text"]

    result, _ = _feishu_call(
        monkeypatch,
        "list_departments",
        {},
        [("GET", "/contact/v3/departments", {"code": 0, "msg": "ok", "data": {"items": [{"name": "研发部"}], "has_more": False}})],
    )
    assert result["isError"] is False
    assert "研发部" in result["content"][0]["text"]

    result, _ = _feishu_call(
        monkeypatch,
        "list_department_users",
        {"department_id": "od_x"},
        [("GET", "/contact/v3/users/find_by_department", {"code": 0, "msg": "ok", "data": {"items": [{"name": "李四"}], "has_more": False}})],
    )
    assert result["isError"] is False
    assert "李四" in result["content"][0]["text"]


def test_bridge_feishu_drive_tools(monkeypatch) -> None:
    """飞书云空间：建文件夹、列文件、读文档内容。"""
    _settings()
    result, _ = _feishu_call(
        monkeypatch,
        "create_folder",
        {"name": "周报"},
        [("POST", "/drive/v1/files/create_folder", {"code": 0, "msg": "ok", "data": {"token": "fld_x"}})],
    )
    assert result["isError"] is False
    assert "fld_x" in result["content"][0]["text"]

    result, _ = _feishu_call(
        monkeypatch,
        "list_files",
        {},
        [("GET", "/drive/v1/files", {"code": 0, "msg": "ok", "data": {"files": [{"name": "a.docx", "token": "t1"}], "has_more": False}})],
    )
    assert result["isError"] is False
    assert "a.docx" in result["content"][0]["text"]

    result, _ = _feishu_call(
        monkeypatch,
        "get_doc_content",
        {"document_id": "doc-9"},
        [("GET", "/docx/v1/documents/doc-9/raw_content", {"code": 0, "msg": "ok", "data": {"content": "正文内容"}})],
    )
    assert result["isError"] is False
    assert "正文内容" in result["content"][0]["text"]


def test_bridge_feishu_calendar_and_task(monkeypatch) -> None:
    """飞书日历与任务：建日程、查日程、建任务。"""
    _settings()
    result, client = _feishu_call(
        monkeypatch,
        "create_event",
        {"calendar_id": "cal_1", "summary": "评审会", "start_timestamp": 1700000000, "end_timestamp": 1700003600},
        [("POST", "/calendar/v4/calendars/cal_1/events", {"code": 0, "msg": "ok", "data": {"event": {"event_id": "ev_1"}}})],
    )
    assert result["isError"] is False
    assert "ev_1" in result["content"][0]["text"]
    sent = client.calls[-1][2]["json"]
    assert sent["start_time"]["timestamp"] == "1700000000"

    result, _ = _feishu_call(
        monkeypatch,
        "list_events",
        {"calendar_id": "cal_1", "start_timestamp": 1700000000},
        [("GET", "/calendar/v4/calendars/cal_1/events", {"code": 0, "msg": "ok", "data": {"items": [{"event_id": "ev_1"}]}})],
    )
    assert result["isError"] is False
    assert "ev_1" in result["content"][0]["text"]

    result, _ = _feishu_call(
        monkeypatch,
        "create_task",
        {"summary": "写周报", "due_timestamp": 1700000000},
        [("POST", "/task/v2/tasks", {"code": 0, "msg": "ok", "data": {"task": {"guid": "task_1"}}})],
    )
    assert result["isError"] is False
    assert "task_1" in result["content"][0]["text"]
