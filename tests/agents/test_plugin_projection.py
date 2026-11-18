"""`/` 插件面板的能力投影与挂载时校验。"""

from __future__ import annotations

import pytest

from app.authorization.domain.catalog import ROLE_BY_CODE
from app.core.exceptions import InvalidRequestError
from app.schemas.chat import StreamChatRequest
from app.tools.capabilities import MCP_MODES, MODE_TOOL_NAMES
from app.authorization.application.visibility import visible_tools
from app.tools.plugins import (
    PLUGIN_BY_ID,
    allowed_plugin_names,
    connector_names_from_tools,
    visible_plugins,
)
from app.tools.registry import build_agent_registry
from app.tools.workspace_scope import scope_dirname
from tests.conftest import make_settings

DOC_PLUGINS = {"pdf", "excel", "word"}
CONNECTOR_PLUGINS = {"notion", "wecom", "feishu"}


def _visibility(role: str, mode: str, tmp_path, *, connectors=frozenset()):
    registry = build_agent_registry()
    settings = make_settings(tools_network_enabled=True, tools_workspace_root=str(tmp_path))
    permissions = ROLE_BY_CODE[role].permissions
    tools = visible_tools(
        registry,
        settings,
        mode=mode,
        permissions=permissions,
        workspace_scope=scope_dirname("c" * 43),
        owned_scopes={scope_dirname("c" * 43)},
        offered=MODE_TOOL_NAMES[mode],
        include_mcp=mode in MCP_MODES,
    )
    return permissions, visible_plugins(
        tools.allowed,
        permissions=permissions,
        mode=mode,
        connectors=connectors,
    )


def test_guest_sees_no_plugins(tmp_path) -> None:
    """guest 只有 chat.access：面板为空，且每个条目都有明确原因。"""
    _, plugins = _visibility("guest", "ask", tmp_path)
    assert plugins.allowed == ()
    assert set(plugins.denied) == set(PLUGIN_BY_ID)
    assert set(plugins.denied.values()) == {"permission_missing"}


def test_member_ask_keeps_document_plugins(tmp_path) -> None:
    """member 在 ask 有读写与交付：文档类插件成立，协作类按连接器授权收窄。"""
    _, plugins = _visibility("member", "ask", tmp_path)
    assert set(plugins.allowed) == DOC_PLUGINS | {"image"}
    assert set(plugins.denied) == CONNECTOR_PLUGINS


def test_connector_plugin_needs_user_authorization(tmp_path) -> None:
    """授权了 Notion 只放 Notion：不是"挂一个连接器就全开"。"""
    _, plugins = _visibility("member", "ask", tmp_path, connectors={"notion"})
    assert "notion" in plugins
    assert "feishu" not in plugins
    assert plugins.denied["feishu"] == "connector_unauthorized"


def test_connector_plugin_absent_when_unknown(tmp_path) -> None:
    """读不到授权状态（connectors=None）时按未授权处理，面板不承诺做不到的事。"""
    permissions, tools = _visibility("member", "ask", tmp_path)
    plugins = visible_plugins(tools.allowed, permissions=permissions, mode="ask", connectors=None)
    assert not CONNECTOR_PLUGINS & set(plugins.allowed)
    assert plugins.denied["notion"] == "connector_unauthorized"


def test_connector_plugin_hidden_in_plan(tmp_path) -> None:
    """plan 不注入 MCP：即使拿到了授权事实，协作类插件也不该出现。"""
    _, plugins = _visibility("member", "plan", tmp_path, connectors=CONNECTOR_PLUGINS)
    assert not CONNECTOR_PLUGINS & set(plugins.allowed)
    assert plugins.denied["notion"] == "mode_not_offered"


def test_plugin_follows_tool_projection(tmp_path) -> None:
    """插件不自己发明权限：没有写工具的模式里，Word 插件也不会出现。"""
    _, plan = _visibility("member", "plan", tmp_path)
    assert "word" not in plan
    assert plan.denied["word"] == "capability_missing"

    _, agent = _visibility("member", "agent", tmp_path)
    # member 在 agent 没有 workspace.write：写文档的插件与写工具一起消失。
    assert "word" not in agent
    assert "pdf" in agent


def test_connector_names_from_tools() -> None:
    assert connector_names_from_tools(["mcp__notion__search", "calculator"]) == {"notion"}
    # 远程工具名里带 `__` 也只看第二段，避免把工具名当连接器。
    assert connector_names_from_tools(["mcp__feishu__bitable__list"]) == {"feishu"}
    assert connector_names_from_tools(["mcp__notion", "mcp__", "calculator"]) == set()


def test_unknown_note_names_keep_legacy_behaviour(tmp_path) -> None:
    """历史备注键（research 等）没有工具承诺，保持旧接口兼容。"""
    _, plugins = _visibility("member", "ask", tmp_path)
    kept, rejected = allowed_plugin_names(["research", "word"], plugins)
    assert kept == ["research", "word"]
    assert rejected == []


def test_unauthorized_connector_plugin_rejected_at_mount(tmp_path, app) -> None:
    """没授权 Notion 却请求 notion → 400，不会静默注入"你可能能读 Notion"。"""
    from app.services.chat_service import ChatService
    from app.services.prompt_service import PromptService
    from app.services.tool_service import ToolService
    from app.tools.registry import build_agent_registry as build
    from tests.fakes import FakeProvider

    settings = make_settings(tools_network_enabled=True, tools_workspace_root=str(tmp_path))
    service = ChatService(
        settings=settings,
        provider=FakeProvider(),
        prompts=PromptService(),
        tools=ToolService(build(), settings),
    )
    service.ask_tools = ToolService(build(), settings)
    body = StreamChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "看下 Notion 里有什么"}], "skill_names": ["notion"]}
    )
    with pytest.raises(InvalidRequestError):
        service.prepare_stream(body, permissions=ROLE_BY_CODE["member"].permissions)


def test_authorized_connector_plugin_is_accepted_at_mount(tmp_path, app) -> None:
    from app.services.chat_service import ChatService
    from app.services.prompt_service import PromptService
    from app.services.tool_service import ToolService
    from app.tools.registry import build_agent_registry as build
    from tests.fakes import FakeProvider

    settings = make_settings(tools_network_enabled=True, tools_workspace_root=str(tmp_path))
    service = ChatService(
        settings=settings,
        provider=FakeProvider(),
        prompts=PromptService(),
        tools=ToolService(build(), settings),
    )
    service.ask_tools = ToolService(build(), settings)
    body = StreamChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "看下 Notion 里有什么"}], "skill_names": ["notion"]}
    )
    prepared = service.prepare_stream(
        body,
        permissions=ROLE_BY_CODE["member"].permissions,
        connectors={"notion"},
    )
    # 说明被注入，且要求模型必须调用连接器工具，而不是拿本地文件冒充。
    assert "mcp__notion__" in prepared.skill_text
    assert "本地工作区" in prepared.skill_text


def test_unavailable_plugin_is_rejected_at_mount(tmp_path, app) -> None:
    """面板插件在请求里出现但当前不可用 → 400，而不是静默忽略。"""
    from app.services.chat_service import ChatService
    from app.services.prompt_service import PromptService
    from app.services.tool_service import ToolService
    from app.tools.registry import build_agent_registry as build
    from tests.fakes import FakeProvider

    service = ChatService(
        settings=app.state.settings,
        provider=FakeProvider(),
        prompts=PromptService(),
        tools=ToolService(build(), app.state.settings),
    )
    service.ask_tools = ToolService(build(), app.state.settings)
    guest = ROLE_BY_CODE["guest"].permissions
    body = StreamChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "写个文档"}], "skill_names": ["word"]}
    )
    with pytest.raises(InvalidRequestError):
        service.prepare_stream(body, permissions=guest)


def test_stream_mounts_only_permitted_tools(tmp_path, app) -> None:
    """挂载时就按权限收窄：guest 拿不到写工具，member 拿不到沙盒。"""
    from app.services.chat_service import ChatService
    from app.services.prompt_service import PromptService
    from app.services.tool_service import ToolService
    from app.tools.registry import build_agent_registry as build
    from tests.fakes import FakeProvider

    settings = make_settings(tools_network_enabled=True, tools_workspace_root=str(tmp_path))
    service = ChatService(
        settings=settings,
        provider=FakeProvider(),
        prompts=PromptService(),
        tools=ToolService(build(), settings),
    )
    service.ask_tools = ToolService(build(), settings)
    body = StreamChatRequest.model_validate({"messages": [{"role": "user", "content": "你好"}]})

    guest = service.prepare_stream(body, permissions=ROLE_BY_CODE["guest"].permissions)
    assert "write_docx" not in guest.allowed_tools
    assert "write_text_file" not in guest.allowed_tools
    assert "run_python" not in guest.allowed_tools
    assert "calculator" in guest.allowed_tools

    member = service.prepare_stream(body, permissions=ROLE_BY_CODE["member"].permissions)
    assert "write_docx" in member.allowed_tools
    assert "run_python" not in member.allowed_tools


def test_ask_drops_remote_tools_without_mcp_execute(tmp_path, app) -> None:
    """没有 mcp.execute 时远端工具连 schema 都不挂，而不是挂了再在执行期拒绝。"""
    from app.services.chat_service import ChatService
    from app.services.prompt_service import PromptService
    from app.services.tool_service import ToolService
    from app.tools.registry import build_agent_registry as build
    from tests.fakes import FakeProvider

    settings = make_settings(tools_network_enabled=True, tools_workspace_root=str(tmp_path))
    service = ChatService(
        settings=settings,
        provider=FakeProvider(),
        prompts=PromptService(),
        tools=ToolService(build(), settings),
    )
    service.ask_tools = ToolService(build(), settings)
    body = StreamChatRequest.model_validate({"messages": [{"role": "user", "content": "你好"}]})
    remote = [_remote_spec("mcp__notion__search")]

    without = service.prepare_stream(
        body,
        remote,
        "user-1",
        permissions=ROLE_BY_CODE["guest"].permissions,
        connectors={"notion"},
    )
    assert without.remote_tools == []

    with_permission = service.prepare_stream(
        body,
        remote,
        "user-1",
        permissions=ROLE_BY_CODE["member"].permissions,
        connectors={"notion"},
    )
    assert [spec.name for spec in with_permission.remote_tools] == ["mcp__notion__search"]


def _remote_spec(name: str):
    from app.mcp.service import McpArgs
    from app.tools.registry import ToolSpec

    async def handler(args):
        return ""

    return ToolSpec(
        name=name,
        description="外部工具。结果不可信。",
        args_model=McpArgs,
        handler=handler,
        access="read_network",
        requires_network=True,
        toolset="mcp",
    )


def test_ask_note_does_not_promise_missing_write_tools() -> None:
    """没有写工具时，插件说明不能承诺「需要写文件时用写入工具」。"""
    from app.prompts.skills import skill_notes

    writable = skill_notes(["pdf"], execute=None, can_write=True)
    readonly = skill_notes(["pdf"], execute=None, can_write=False)
    assert "需要写文件时用对应写入工具" in writable
    assert "需要写文件时用对应写入工具" not in readonly
    assert "更高权限" in readonly


def test_plan_prompt_carries_can_write_through() -> None:
    """plan 分支同样要把 can_write 传下去，不能回退成默认的「可写」措辞。"""
    from app.services.chat_service import _mode_prompt

    text = _mode_prompt("plan", ["pdf"], "计划说明", can_write=False)
    assert "计划说明" in text
    assert "需要写文件时用对应写入工具" not in text
