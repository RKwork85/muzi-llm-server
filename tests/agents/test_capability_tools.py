"""Ask / Agent 能力边界，以及天气、写入、删除和沙箱命令。"""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from pydantic import ValidationError

from app.agents.toolsets import agent_tool_names, ask_tool_names, chat_mode_tool_names, plan_tool_names
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
from app.tools.workspace_scope import scope_dirname
from tests.conftest import make_settings


def test_capability_matrix_projects_tools_from_permissions(tmp_path) -> None:
    """名单是权限投影：工具出现即代表它已获授权，不再需要单独的 grants。"""
    from app.authorization.domain.catalog import ROLE_BY_CODE

    registry = build_agent_registry()
    settings = make_settings(tools_network_enabled=True, tools_workspace_root=str(tmp_path))
    member = ROLE_BY_CODE["member"].permissions
    developer = ROLE_BY_CODE["developer"].permissions

    ask = set(ask_tool_names(registry, settings, permissions=member))
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
    assert "run_python" not in ask  # member 没有 ask.command.execute
    assert supports_orchestration("ask") is False

    plan = set(plan_tool_names(registry, settings, permissions=member))
    assert {"web_search", "read_text_file", "calculator"} <= plan
    assert "write_text_file" not in plan
    assert "write_docx" not in plan
    assert "deliver_file" not in plan
    assert supports_orchestration("plan") is False

    agent = set(agent_tool_names(registry, settings, None, permissions=member))
    assert "weather_query" in agent
    assert "read_text_file" in agent
    assert "write_text_file" not in agent
    assert "write_docx" not in agent
    assert "delete_file" not in agent
    assert "run_command" not in agent
    assert supports_orchestration("agent") is True

    # developer 具备 agent 写/执行权限时，同一个 toolset 直接带出写工具。
    scope = scope_dirname("c" * 43)
    opened = set(
        agent_tool_names(
            registry,
            settings,
            ["workspace"],
            permissions=developer,
            workspace_scope=scope,
            owned_scopes={scope},
        )
    )
    assert {"write_text_file", "write_docx", "delete_file", "run_command"} <= opened

    # 没有权限时不会出现；不再有「传了 grant 就能开」的第二条路径。
    assert "write_docx" not in set(agent_tool_names(registry, settings, ["workspace"], permissions=member))


def test_ask_mode_masks_skill_tools(tmp_path) -> None:
    """Ask 模式屏蔽 Skill：不暴露发现/加载/读取技能工具；Agent 保持完整能力。

    Plan 已收敛到交互式规划子系统（app/planning），对话通道按兼容口径返回空名单，
    只读工具由规划引擎按 PLAN_TOOLS 白名单挂载。
    """
    registry = build_agent_registry()
    settings = make_settings(skills_root=str(tmp_path / "skills"))
    ask = set(chat_mode_tool_names(registry, settings, "ask"))
    assert "discover_skills" not in ask
    assert "load_skill" not in ask
    assert "read_skill_resource" not in ask

    agent = set(chat_mode_tool_names(registry, settings, "agent"))
    assert {"discover_skills", "load_skill", "read_skill_resource"} <= agent

    assert chat_mode_tool_names(registry, settings, "plan") == ()


def test_explicit_workspace_toolset_does_not_grant_write_or_delete(tmp_path) -> None:
    from app.authorization.domain.catalog import ROLE_BY_CODE

    registry = build_agent_registry()
    settings = make_settings(tools_workspace_root=str(tmp_path))
    names = set(
        agent_tool_names(
            registry,
            settings,
            ["workspace"],
            permissions=ROLE_BY_CODE["member"].permissions,
        )
    )
    assert names == {"list_directory", "read_text_file", "search_text"}


def test_code_toolset_unlocks_run_python_but_run_command_needs_grant(tmp_path) -> None:
    """工具集只能收窄：真正的可见性由 capbility.action 对应的权限码决定。

    - developer（有 agent.command.execute）传 ['skills','code']：run_python 与技能脚本进名单。
    - member（只有 skill.execute）传 ['skills']：技能说明可读，但技能脚本要求
      command.execute，所以不会出现，更不会出现「传了 code 却被执行层拒」的空转。
    """
    registry = build_agent_registry()
    settings = make_settings(
        tools_workspace_root=str(tmp_path),
        skills_root=str(tmp_path / "skills"),
    )

    scope = scope_dirname("d" * 43)
    developer = set(
        agent_tool_names(
            registry,
            settings,
            toolsets=["skills", "code"],
            permissions={"agent.execute", "skill.execute", "agent.command.execute"},
            workspace_scope=scope,
            owned_scopes={scope},
        )
    )
    assert {"run_python", "discover_skills", "load_skill", "read_skill_resource", "execute_skill_script"} <= developer
    assert "run_command" not in developer

    member = set(
        agent_tool_names(
            registry,
            settings,
            toolsets=["skills"],
            permissions={"agent.execute", "skill.execute"},
        )
    )
    assert {"discover_skills", "load_skill", "read_skill_resource"} <= member
    assert "execute_skill_script" not in member
    assert "run_python" not in member
    assert "run_command" not in member


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


# 1x1 PNG：只用来验证图片块能进包，不依赖 PIL。
_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGOUCzjBwMDAxAAGAA4IAToauT++AAAAAElFTkSuQmCC"
)


def test_write_docx_supports_table_image_and_landscape(tmp_path) -> None:
    import zipfile

    (tmp_path / "logo.png").write_bytes(_PNG_1PX)
    token = CURRENT_SETTINGS.set(make_settings(tools_workspace_root=str(tmp_path)))
    try:
        written = json.loads(
            write_docx(
                WriteDocxArgs(
                    path="out/report.docx",
                    title="季报",
                    orientation="landscape",
                    blocks=[
                        DocBlock(kind="heading", text="概览", level=1),
                        DocBlock(
                            kind="table",
                            headers=["指标", "本期", "同比"],
                            rows=[["营收", "4.28", "+15.0%"], ["毛利", "41.2%", "+2.6pp"]],
                            caption="表 1 财务对比",
                        ),
                        DocBlock(kind="image", path="logo.png", width_mm=40),
                        DocBlock(kind="page_break"),
                        DocBlock(kind="paragraph", text="第二页"),
                    ],
                )
            )
        )
        assert written["created"] is True
        package = zipfile.ZipFile(tmp_path / "out" / "report.docx")
        xml = package.read("word/document.xml").decode("utf-8")
        assert 'w:w="16838"' in xml and 'w:h="11906"' in xml  # 横向 A4
        assert "<w:tbl>" in xml
        assert 'w:fill="1F3864"' in xml
        assert "<w:drawing>" in xml
        assert "表 1 财务对比" in xml
        assert any(name.startswith("word/media/") for name in package.namelist())

        missing = json.loads(
            write_docx(WriteDocxArgs(path="a.docx", blocks=[DocBlock(kind="image", path="nope.png")]))
        )
        assert missing["error"]["code"] == "image_missing"
        escape = json.loads(
            write_docx(WriteDocxArgs(path="b.docx", blocks=[DocBlock(kind="image", path="../x.png")]))
        )
        assert escape["error"]["code"] == "file_unavailable"
    finally:
        CURRENT_SETTINGS.reset(token)


def test_doc_block_rejects_ragged_table() -> None:
    with pytest.raises(ValidationError):
        DocBlock(kind="table", headers=["a", "b"], rows=[["1"]])
    with pytest.raises(ValidationError):
        DocBlock(kind="heading", text="   ")


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


def test_run_python_runs_in_ephemeral_sandbox_and_hides_local_paths(tmp_path) -> None:
    token = CURRENT_SETTINGS.set(make_settings(tools_workspace_root=str(tmp_path), tools_sandbox_timeout_seconds=5))
    try:
        code = (
            "import os\n"
            "print(6 * 7)\n"
            "print(os.getcwd())\n"
            "open('note.txt', 'w', encoding='utf-8').write('hello-code')\n"
            "print(open('note.txt', encoding='utf-8').read())\n"
        )
        payload = json.loads(run_python(PythonArgs(code=code)))
        missing = json.loads(run_python(PythonArgs(code="raise RuntimeError('boom')\n")))
    finally:
        CURRENT_SETTINGS.reset(token)
    assert payload["exit_code"] == 0
    assert "42" in payload["output"]
    assert "hello-code" in payload["output"]
    assert str(tmp_path) not in payload["output"]
    assert "muzi_py_" not in payload["output"]
    # 阅后即焚：相对路径写出的文件只落在临时目录，不残留到 workspace
    assert not (tmp_path / "note.txt").exists()
    assert missing["exit_code"] != 0
    assert "boom" in missing["output"]
    assert str(tmp_path) not in missing["output"]
    assert "muzi_py_" not in missing["output"]


def test_run_python_times_out_and_needs_settings(tmp_path) -> None:
    token = CURRENT_SETTINGS.set(make_settings(tools_workspace_root=str(tmp_path), tools_sandbox_timeout_seconds=0.2))
    try:
        timed_out = json.loads(run_python(PythonArgs(code="import time\ntime.sleep(5)\n")))
    finally:
        CURRENT_SETTINGS.reset(token)
    assert timed_out["error"]["code"] == "code_timeout"
    denied = json.loads(run_python(PythonArgs(code="print(1)\n")))
    assert denied["error"]["code"] == "code_unavailable"


def test_sandbox_command_runs_allowlisted_name_in_ephemeral_dir(tmp_path) -> None:
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
    assert "muzi_cmd_" not in payload["output"]


def test_update_todos_is_projected_only_for_plan_runs(tmp_path) -> None:
    """清单工具只在「计划交接而来的运行」出现，普通 Agent 对话看不到。"""
    from app.agents.toolsets import agent_chat_tool_names
    from app.authorization.application.execution_guard import CURRENT_EXECUTION, ExecutionContext
    from app.authorization.domain.catalog import ROLE_BY_CODE

    registry = build_agent_registry()
    settings = make_settings(tools_workspace_root=str(tmp_path))
    permissions = frozenset(ROLE_BY_CODE["developer"].permissions)

    without_plan = set(agent_chat_tool_names(registry, settings, permissions=permissions))
    assert "update_todos" not in without_plan
    assert "update_todos" not in set(plan_tool_names(registry, settings, permissions=permissions))
    assert "update_todos" not in set(ask_tool_names(registry, settings, permissions=permissions))

    context = ExecutionContext(user_id="u_plan", runtime_mode="agent", plan_id="plan_abc", effective_permissions=permissions)
    token = CURRENT_EXECUTION.set(context)
    try:
        with_plan = set(agent_chat_tool_names(registry, settings, permissions=permissions))
    finally:
        CURRENT_EXECUTION.reset(token)
    assert "update_todos" in with_plan
