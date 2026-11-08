"""Ask / Agent 能力边界，以及天气、写入、删除和沙箱命令。"""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from app.agents.toolsets import agent_tool_names, ask_tool_names, plan_tool_names
from app.services.chat_service import _download_files
from app.tools.capabilities import supports_orchestration
from app.data.context import CURRENT_OSS_CLIENT, CURRENT_TASK_ID
from app.data.oss_client import OssUploadClient
from app.tools.deliver_file import DeliverArgs, deliver_file
from app.tools.docx_tool import DocBlock, WriteDocxArgs, write_docx
from app.tools.file_changes import DeleteArgs, WriteArgs, delete_file, write_text_file
from app.tools.registry import build_agent_registry
from app.tools.code_runner import PythonArgs, run_python
from app.tools.sandbox_command import CommandArgs, run_command
from app.tools.weather import WeatherArgs, query_weather
from app.tools.web_tools import CURRENT_SETTINGS
from tests.conftest import make_settings


def test_capability_matrix_keeps_destructive_tools_behind_grants(tmp_path) -> None:
    registry = build_agent_registry()
    settings = make_settings(tools_network_enabled=True, tools_workspace_root=str(tmp_path))
    ask = set(ask_tool_names(registry, settings))
    assert {
        "web_search",
        "web_fetch",
        "search_text",
        "read_text_file",
        "calculator",
        "weather_query",
        "write_text_file",
        "write_docx",
        "create_flowchart",
    } <= ask
    assert "delete_file" not in ask
    assert "run_command" not in ask
    assert "run_python" in ask
    assert supports_orchestration("ask") is False
    plan = set(plan_tool_names(registry, settings))
    assert {"web_search", "read_text_file", "calculator"} <= plan
    assert "write_text_file" not in plan
    assert "write_docx" not in plan
    assert "deliver_file" not in plan
    assert supports_orchestration("plan") is False

    agent = set(agent_tool_names(registry, settings, None, None))
    assert "weather_query" in agent
    assert "read_text_file" in agent
    assert "write_text_file" not in agent
    assert "write_docx" not in agent
    assert "delete_file" not in agent
    assert "run_command" not in agent
    assert supports_orchestration("agent") is True

    opened = set(
        agent_tool_names(
            registry,
            settings,
            None,
            ["write_file", "delete_file", "run_command"],
        )
    )
    assert {"write_text_file", "write_docx", "delete_file", "run_command"} <= opened


def test_explicit_workspace_toolset_does_not_grant_write_or_delete(tmp_path) -> None:
    registry = build_agent_registry()
    settings = make_settings(tools_workspace_root=str(tmp_path))
    names = set(agent_tool_names(registry, settings, ["workspace"], None))
    assert names == {"list_directory", "read_text_file", "search_text"}


def test_write_docx_is_a_real_word_package_and_text_writer_refuses_it(tmp_path) -> None:
    import xml.etree.ElementTree as ET
    import zipfile

    token = CURRENT_SETTINGS.set(make_settings(tools_workspace_root=str(tmp_path)))
    try:
        refused = json.loads(write_text_file(WriteArgs(path="说明.docx", content="hello")))
        assert refused["error"]["message"] == "Word 文档请使用 write_docx"
        written = json.loads(
            write_docx(
                WriteDocxArgs(
                    path="docs/说明.docx",
                    title="说明",
                    blocks=[
                        DocBlock(kind="heading", text="第一节", level=2),
                        DocBlock(kind="paragraph", text="正文 <script>"),
                        DocBlock(kind="bullet", text="要点"),
                    ],
                )
            )
        )
        assert written["source"] == "docs/说明.docx"
        assert written["created"] is True
        package = zipfile.ZipFile(tmp_path / "docs" / "说明.docx")
        xml = package.read("word/document.xml").decode("utf-8")
        ET.fromstring(xml)
        assert "说明" in xml
        assert "第一节" in xml
        assert "要点" in xml
        assert "<script>" not in xml
        assert "&lt;script&gt;" in xml
        denied = json.loads(write_docx(WriteDocxArgs(path="../outside.docx", blocks=[DocBlock(kind="paragraph", text="x")])))
        assert denied["error"]["code"] == "file_unavailable"
    finally:
        CURRENT_SETTINGS.reset(token)


def test_write_and_delete_stay_inside_workspace(tmp_path) -> None:
    token = CURRENT_SETTINGS.set(make_settings(tools_workspace_root=str(tmp_path)))
    try:
        written = json.loads(write_text_file(WriteArgs(path="notes/a.txt", content="hello")))
        assert written["source"] == "notes/a.txt"
        assert written["created"] is True
        assert (tmp_path / "notes" / "a.txt").read_text(encoding="utf-8") == "hello"
        removed = json.loads(delete_file(DeleteArgs(path="notes/a.txt")))
        assert removed["deleted"] is True
        assert not (tmp_path / "notes" / "a.txt").exists()
        denied = json.loads(delete_file(DeleteArgs(path="../outside.txt")))
        assert denied["error"]["code"] == "file_unavailable"
    finally:
        CURRENT_SETTINGS.reset(token)


async def test_weather_reads_open_meteo_without_a_caller_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host.startswith("geocoding"):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "name": "北京",
                            "country": "中国",
                            "latitude": 39.9,
                            "longitude": 116.4,
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "current": {
                    "temperature_2m": 21.5,
                    "relative_humidity_2m": 40,
                    "weather_code": 0,
                    "wind_speed_10m": 3,
                }
            },
        )

    with pytest.raises(ValidationError):
        WeatherArgs(place="https://example.com")
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        payload = json.loads(await query_weather(client, "北京"))
    assert payload["place"] == "北京"
    assert payload["condition"] == "晴"
    assert payload["temperature_c"] == 21.5
    assert payload["untrusted"] is True


async def test_deliver_file_returns_the_oss_url(tmp_path) -> None:
    (tmp_path / "清单.csv").write_bytes(b"a,b\n")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/upload/single"
        assert "upload_path" in str(request.url)
        return httpx.Response(
            200,
            json={
                "data": {
                    "success": True,
                    "url": "https://bucket.oss.example.com/agent-data/ask/清单.csv",
                }
            },
        )

    client = OssUploadClient(
        "http://127.0.0.1:8890",
        timeout=5,
        prefix="agent-data",
        transport=httpx.MockTransport(handler),
    )
    settings = make_settings(
        tools_workspace_root=str(tmp_path),
        oss_upload_base_url="http://127.0.0.1:8890",
    )
    tokens = [
        (CURRENT_SETTINGS, CURRENT_SETTINGS.set(settings)),
        (CURRENT_OSS_CLIENT, CURRENT_OSS_CLIENT.set(client)),
        (CURRENT_TASK_ID, CURRENT_TASK_ID.set("ask-1")),
    ]
    try:
        payload = json.loads(await deliver_file(DeliverArgs(path="清单.csv")))
        denied = json.loads(await deliver_file(DeliverArgs(path="../secret.txt")))
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
    assert payload["ok"] is True
    assert payload["kind"] == "user_download"
    assert payload["download_url"] == "https://bucket.oss.example.com/agent-data/ask/清单.csv"
    assert payload["file_name"] == "清单.csv"
    assert str(tmp_path) not in json.dumps(payload)
    assert denied["ok"] is False
    registry = build_agent_registry()
    assert "deliver_file" in ask_tool_names(registry, settings)
    assert "deliver_file" not in ask_tool_names(
        registry, make_settings(tools_workspace_root=str(tmp_path))
    )
    wrapped = json.dumps(
        {"source": "server_tool", "name": "deliver_file", "result": json.dumps(payload, ensure_ascii=False)},
        ensure_ascii=False,
    )
    files = _download_files([{"role": "tool", "content": wrapped}])
    assert files[0]["kind"] == "user_download"
    assert files[0]["download_url"] == payload["download_url"]


def test_sandbox_command_rejects_unlisted_and_escaping_arguments(tmp_path) -> None:
    token = CURRENT_SETTINGS.set(
        make_settings(tools_workspace_root=str(tmp_path), tools_sandbox_commands="hostname")
    )
    try:
        denied = json.loads(run_command(CommandArgs(command="python", args=["script.py"])))
        assert denied["error"]["code"] == "command_not_authorized"
        with pytest.raises(ValidationError):
            CommandArgs(command="hostname", args=["../secret"])
        with pytest.raises(ValidationError):
            CommandArgs(command="cmd", args=["/c", "whoami"])
    finally:
        CURRENT_SETTINGS.reset(token)


def test_run_python_executes_in_workspace_and_hides_local_paths(tmp_path) -> None:
    marker = tmp_path / "note.txt"
    marker.write_text("hello-code", encoding="utf-8")
    token = CURRENT_SETTINGS.set(make_settings(tools_workspace_root=str(tmp_path), tools_sandbox_timeout_seconds=5))
    try:
        payload = json.loads(run_python(PythonArgs(code="print(6 * 7)\nprint(open('note.txt', encoding='utf-8').read())\n")))
        missing = json.loads(run_python(PythonArgs(code="raise RuntimeError('boom')\n")))
    finally:
        CURRENT_SETTINGS.reset(token)
    assert payload["exit_code"] == 0
    assert "42" in payload["output"]
    assert "hello-code" in payload["output"]
    assert str(tmp_path) not in payload["output"]
    assert missing["exit_code"] != 0
    assert "boom" in missing["output"]
    assert str(tmp_path) not in missing["output"]


def test_run_python_times_out_and_needs_a_workspace(tmp_path) -> None:
    token = CURRENT_SETTINGS.set(make_settings(tools_workspace_root=str(tmp_path), tools_sandbox_timeout_seconds=0.2))
    try:
        timed_out = json.loads(run_python(PythonArgs(code="import time\ntime.sleep(5)\n")))
    finally:
        CURRENT_SETTINGS.reset(token)
    assert timed_out["error"]["code"] == "code_timeout"
    denied = json.loads(run_python(PythonArgs(code="print(1)\n")))
    assert denied["error"]["code"] == "code_unavailable"


def test_sandbox_command_runs_allowlisted_name_in_workspace(tmp_path) -> None:
    token = CURRENT_SETTINGS.set(
        make_settings(tools_workspace_root=str(tmp_path), tools_sandbox_commands="hostname")
    )
    try:
        payload = json.loads(run_command(CommandArgs(command="hostname")))
    finally:
        CURRENT_SETTINGS.reset(token)
    assert payload["command"] == "hostname"
    assert payload["exit_code"] == 0
    assert payload["output"].strip()
    assert str(tmp_path) not in payload["output"]
