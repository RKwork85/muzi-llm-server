"""工具执行链的授权测试。权限来自角色目录，不使用始终放行的守卫。"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.agents.executor import ToolExecutor
from app.agents.policy import RunClock, server_policy_from_settings
from app.agents.toolsets import agent_tool_names, ask_tool_names, plan_tool_names
from app.authorization.application.authorization_service import AuthorizationService
from app.authorization.application.execution_guard import CURRENT_EXECUTION, ExecutionContext, load_execution_context
from app.authorization.domain.catalog import BUILTIN_ROLES, PERMISSION_BY_CODE, PERMISSION_CATALOG, ROLE_BY_CODE
from app.core.exceptions import PermissionDeniedError, ToolNotAllowedError
from app.mcp.grants import stdio_permitted
from app.providers.base import ToolCall
from app.services.auth_service import AuthService
from app.services.tool_service import ToolService
from app.tools.registry import ToolRegistry, ToolSpec, build_agent_registry
from app.tools.stdio_access import CURRENT_MCP_USER, stdio_access
from app.tools.workspace_scope import CURRENT_WORKSPACE_SCOPE, scope_dirname
from tests.conftest import make_settings
from tests.fakes import FakeProvider, chat_payload, make_result, make_tool_call

_MEMBER = ROLE_BY_CODE["member"].permissions
_LEGACY_TOOL_CODES = frozenset({"workspace.read", "workspace.write", "workspace.delete", "command.execute"})


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def commit(self):
        return None


def test_tool_permissions_are_mode_scoped_and_default_granted() -> None:
    codes = {spec.code for spec in PERMISSION_CATALOG}
    # 旧的、无模式前缀的工具权限码已移除
    assert _LEGACY_TOOL_CODES.isdisjoint(codes)
    # 新模式化的工具权限码都在目录里
    for mode in ("ask", "plan", "agent"):
        for suffix in ("workspace.read", "workspace.write", "workspace.delete", "command.execute"):
            assert f"{mode}.{suffix}" in codes

    member = ROLE_BY_CODE["member"].permissions
    developer = ROLE_BY_CODE["developer"].permissions
    guest = ROLE_BY_CODE["guest"].permissions

    # member：ask 可读写、plan 只读、agent 只读
    assert "ask.workspace.read" in member and "ask.workspace.write" in member
    assert "ask.workspace.delete" not in member and "ask.command.execute" not in member
    assert "plan.workspace.read" in member
    assert "agent.workspace.read" in member and "agent.workspace.write" not in member

    # developer：ask/agent 具备完整工具执行能力，plan 仍只读
    assert "agent.workspace.write" in developer and "agent.workspace.delete" in developer
    assert "agent.command.execute" in developer
    assert "plan.workspace.write" not in developer and "plan.workspace.delete" not in developer

    # guest 无任何工具执行权限
    assert not (guest & {c for c in codes if c.split(".")[0] in ("ask", "plan", "agent")})


def _context(mode: str, permissions: set[str] | frozenset[str], owned: frozenset[str] = frozenset()) -> ExecutionContext:
    return ExecutionContext(
        user_id=str(uuid.uuid4()),
        runtime_mode=mode,
        effective_permissions=frozenset(permissions),
        owned_workspace_scopes=owned,
        authorization_source="rbac",
    )


def _install_roles(monkeypatch, roles: dict[str, str]) -> dict[str, SimpleNamespace]:
    users = {token: SimpleNamespace(id=uuid.uuid4(), status="active") for token in roles}

    async def user_for_token(_self, raw: str):
        return users.get(raw)

    def permissions_for(user_id: object) -> set[str]:
        role = next(roles[token] for token, user in users.items() if user.id == user_id)
        if role == "super_admin":
            return set(PERMISSION_BY_CODE)
        return set(ROLE_BY_CODE[role].permissions)

    async def require_permission(_self, user_id, permission, resource_id=None, context=None):
        del resource_id, context
        if permission not in permissions_for(user_id):
            raise PermissionDeniedError()

    async def get_effective_permissions(_self, user_id):
        return permissions_for(user_id)

    monkeypatch.setattr("app.api.agent_access.get_session_factory", lambda: (lambda: _Session()))
    monkeypatch.setattr("app.core.database.session.get_session_factory", lambda: (lambda: _Session()))
    monkeypatch.setattr(AuthService, "user_for_token", user_for_token)
    monkeypatch.setattr(AuthorizationService, "require_permission", require_permission)
    monkeypatch.setattr(AuthorizationService, "get_effective_permissions", get_effective_permissions)
    return users


async def _execute(tool_name: str, arguments: str, *, permissions: set[str] | None, mode: str = "agent", owned: frozenset[str] = frozenset(), scope: str | None = None):
    registry = build_agent_registry()
    spec = registry.get(tool_name)
    assert spec is not None
    called: list[object] = []
    original = spec.handler

    def spy(args):
        called.append(args)
        return original(args)

    spec.handler = spy
    policy = server_policy_from_settings(make_settings(), registry).model_copy(
        update={"allowed_tools": tuple(sorted({tool_name, "calculator"}))}
    )
    executor = ToolExecutor(registry, policy.allowed_tools)
    token = None
    scope_token = None
    if permissions is not None:
        token = CURRENT_EXECUTION.set(_context(mode, permissions, owned))
    if scope is not None:
        scope_token = CURRENT_WORKSPACE_SCOPE.set(scope)
    try:
        result = await executor.execute(
            tool_call_id="call_tool_auth",
            tool_name=tool_name,
            arguments=arguments,
            allowlist=policy.allowed_tools,
            clock=RunClock.start(policy),
        )
    finally:
        if scope_token is not None:
            CURRENT_WORKSPACE_SCOPE.reset(scope_token)
        if token is not None:
            CURRENT_EXECUTION.reset(token)
        spec.handler = original
    return result, called


async def test_member_can_run_basic_tool_and_cannot_write_delete_or_command() -> None:
    allowed, called = await _execute("calculator", '{"expression":"23 * 19"}', permissions=set(_MEMBER))
    assert allowed.status.value == "succeeded"
    assert called

    for name, arguments in (
        ("write_text_file", '{"path":"a.txt","content":"secret"}'),
        ("delete_file", '{"path":"a.txt"}'),
        ("run_command", '{"command":"hostname"}'),
        ("run_python", '{"code":"print(1)"}'),
    ):
        result, called = await _execute(name, arguments, permissions=set(_MEMBER))
        assert result.status.value == "rejected"
        assert result.error_code == "PERMISSION_DENIED"
        assert called == []


async def test_missing_identity_and_forged_grants_do_not_execute() -> None:
    result, called = await _execute(
        "write_text_file",
        '{"path":"a.txt","content":"x","permission":"workspace.write","user_id":"admin"}',
        permissions=None,
    )
    assert result.error_code == "IDENTITY_REQUIRED"
    assert called == []

    registry = build_agent_registry()
    settings = make_settings()
    forged = agent_tool_names(registry, settings, None, ["write_file", "delete_file", "run_command"], permissions=_MEMBER)
    assert "write_text_file" not in forged
    assert "delete_file" not in forged
    assert "run_command" not in forged
    ask = set(ask_tool_names(registry, settings))
    plan = set(plan_tool_names(registry, settings))
    assert "delete_file" not in ask
    assert "run_command" not in ask
    assert "write_text_file" not in plan


async def test_runtime_modes_keep_their_boundaries() -> None:
    write_args = '{"path":"a.txt","content":"x"}'
    ask_result, called = await _execute("run_command", '{"command":"hostname"}', permissions=set(_MEMBER | {"chat.access"}), mode="ask")
    assert ask_result.error_code == "PERMISSION_DENIED"
    assert called == []

    owned = frozenset({scope_dirname("a" * 43)})
    plan_result, called = await _execute(
        "write_text_file",
        write_args,
        permissions=set(_MEMBER | {"chat.access"}),  # member 无 plan.workspace.write
        mode="plan",
        owned=owned,
        scope=scope_dirname("a" * 43),
    )
    assert plan_result.error_code == "PERMISSION_DENIED"
    assert called == []

    skill_result, called = await _execute(
        "discover_skills",
        "{}",
        permissions=set(_MEMBER - {"skill.execute"}),
        mode="agent",
    )
    assert skill_result.error_code == "PERMISSION_DENIED"
    assert called == []


async def test_ask_mode_tool_permissions_follow_mode_scoped_codes() -> None:
    """ask 模式：按 ask.workspace.read/write 等模式化权限码判定，不再有硬编码放宽。"""
    from app.authorization.application.execution_guard import ExecutionGuard

    token = CURRENT_EXECUTION.set(_context("ask", set(_MEMBER | {"chat.access"})))
    guard = ExecutionGuard()
    try:
        write = guard.decide(tool_name="write_docx", toolset="workspace", access="write_workspace")
        assert write.allowed is True
        assert write.audit is True

        deliver = guard.decide(tool_name="deliver_file", toolset="delivery", access="write_remote")
        assert deliver.allowed is True

        delete = guard.decide(tool_name="delete_file", toolset="workspace", access="delete_workspace")
        assert delete.allowed is False
        assert delete.reason == "permission_denied"

        run = guard.decide(tool_name="run_python", toolset="code", access="sandbox_exec")
        assert run.allowed is False
        assert run.reason == "permission_denied"
    finally:
        CURRENT_EXECUTION.reset(token)


async def test_workspace_key_is_not_ownership() -> None:
    key_a = "a" * 43
    key_b = "b" * 43
    owned = frozenset({scope_dirname(key_a)})
    permissions = set(_MEMBER | {"agent.workspace.write", "agent.workspace.delete", "agent.command.execute"})
    result, called = await _execute(
        "write_text_file",
        '{"path":"a.txt","content":"cross"}',
        permissions=permissions,
        owned=owned,
        scope=scope_dirname(key_b),
    )
    assert result.error_code == "WORKSPACE_UNVERIFIED"
    assert called == []
    deleted, called = await _execute(
        "delete_file",
        '{"path":"a.txt"}',
        permissions=permissions,
        owned=owned,
        scope=scope_dirname(key_b),
    )
    assert deleted.error_code == "WORKSPACE_UNVERIFIED"
    assert called == []
    unverified, called = await _execute(
        "run_command",
        '{"command":"hostname"}',
        permissions=permissions,
        owned=frozenset(),
        scope=scope_dirname(key_a),
    )
    assert unverified.error_code == "WORKSPACE_UNVERIFIED"
    assert called == []


async def test_database_outage_does_not_open_sensitive_tools(monkeypatch) -> None:
    def unavailable():
        raise RuntimeError("PostgreSQL 未初始化")

    monkeypatch.setattr("app.core.database.session.get_session_factory", unavailable)
    context = await load_execution_context("00000000-0000-0000-0000-000000000001", runtime_mode="agent")
    assert context.authorization_source == "unavailable"
    assert context.effective_permissions == frozenset()
    assert context.owned_workspace_scopes == frozenset()
    token = CURRENT_EXECUTION.set(context)
    try:
        result, called = await _execute("write_text_file", '{"path":"a.txt","content":"x"}', permissions=None)
    finally:
        CURRENT_EXECUTION.reset(token)
    assert result.error_code == "IDENTITY_REQUIRED" or result.error_code == "PERMISSION_DENIED"
    assert called == []


async def test_mcp_permission_and_revocation_both_apply() -> None:
    called: list[str] = []

    async def handler(_args):
        called.append("ran")
        if not await stdio_permitted(CURRENT_MCP_USER.get(), "mcp__demo__echo"):
            raise PermissionError("mcp user denied")
        return "ok"

    from pydantic import BaseModel, ConfigDict

    class EchoArgs(BaseModel):
        model_config = ConfigDict(extra="forbid")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="mcp__demo__echo",
            description="echo",
            args_model=EchoArgs,
            handler=handler,
            toolset="mcp",
            access="read_local",
        )
    )
    policy = server_policy_from_settings(make_settings(), build_agent_registry()).model_copy(
        update={"allowed_tools": ("mcp__demo__echo",)}
    )
    executor = ToolExecutor(registry, policy.allowed_tools)
    user = "mcp-user-a"
    stdio_access.set_catalog({"mcp__demo__echo"})
    stdio_access.grant(user, {"mcp__demo__echo"})
    denied = CURRENT_EXECUTION.set(_context("agent", set(_MEMBER)))
    user_token = CURRENT_MCP_USER.set(user)
    try:
        blocked = await executor.execute(
            tool_call_id="call_mcp",
            tool_name="mcp__demo__echo",
            arguments="{}",
            allowlist=policy.allowed_tools,
            clock=RunClock.start(policy),
        )
        assert blocked.error_code == "PERMISSION_DENIED"
        assert called == []
        CURRENT_EXECUTION.reset(denied)
        allowed_token = CURRENT_EXECUTION.set(_context("agent", set(_MEMBER | {"mcp.execute"})))
        first = await executor.execute(
            tool_call_id="call_mcp_ok",
            tool_name="mcp__demo__echo",
            arguments="{}",
            allowlist=policy.allowed_tools,
            clock=RunClock.start(policy),
        )
        assert first.status.value == "succeeded"
        stdio_access.revoke(user)
        second = await executor.execute(
            tool_call_id="call_mcp_revoked",
            tool_name="mcp__demo__echo",
            arguments="{}",
            allowlist=policy.allowed_tools,
            clock=RunClock.start(policy),
        )
        assert second.status.value != "succeeded"
        assert await stdio_permitted(user, "mcp__demo__echo") is False
        assert await stdio_permitted("other-user", "mcp__demo__echo") is False
    finally:
        CURRENT_MCP_USER.reset(user_token)
        CURRENT_EXECUTION.set(None)
        stdio_access.set_catalog(set())


async def test_ask_tool_service_rejects_unauthenticated_write_and_keeps_calculator() -> None:
    registry = build_agent_registry()
    service = ToolService(registry, make_settings())
    called: list[object] = []
    spec = registry.get("write_text_file")
    assert spec is not None
    original = spec.handler

    def spy(args):
        called.append(args)
        return original(args)

    spec.handler = spy
    try:
        try:
            await service.execute(
                [ToolCall(id="call_write", name="write_text_file", arguments='{"path":"a.txt","content":"x"}')],
                allowed=frozenset({"write_text_file"}),
            )
            raise AssertionError("unauthenticated write was executed")
        except ToolNotAllowedError:
            assert called == []
        messages = await service.execute(
            [ToolCall(id="call_calc", name="calculator", arguments='{"expression":"1+1"}')],
            allowed=frozenset({"calculator"}),
        )
    finally:
        spec.handler = original
    assert "2" in messages[0]["content"]


def test_agent_api_ignores_client_grants(client: TestClient, provider: FakeProvider, monkeypatch) -> None:
    _install_roles(monkeypatch, {"token-member-0000001": "member"})
    provider.push(
        make_result(
            content=None,
            tool_calls=[make_tool_call("call_w", "write_text_file", '{"path":"a.txt","content":"secret-grant"}')],
        )
    )
    provider.push(make_result("结束"))
    created = client.post(
        "/api/v1/agents/runs",
        headers={"Authorization": "Bearer token-member-0000001", "X-Workspace-Key": "b" * 43},
        json={
            "message": "写入文件",
            "prompt_name": "default",
            "grants": ["write_file", "delete_file", "run_command"],
        },
    )
    assert created.status_code == 200
    task_id = created.json()["task_id"]
    finished = None
    for _ in range(40):
        body = client.get(f"/api/v1/agents/runs/{task_id}", headers={"Authorization": "Bearer token-member-0000001"}).json()
        if body["status"] in {"completed", "failed", "cancelled", "limit_reached"}:
            finished = body
            break
    assert finished is not None
    offered = [item["function"]["name"] for item in (provider.calls[0].tools or [])]
    rendered = json.dumps([call.messages for call in provider.calls], ensure_ascii=False)
    assert "write_text_file" not in offered
    assert "TOOL_NOT_ALLOWED" in rendered or "PERMISSION_DENIED" in rendered


def test_chat_agent_mode_still_requires_execute(client: TestClient, provider: FakeProvider, monkeypatch) -> None:
    _install_roles(monkeypatch, {"token-guest-00000001": "guest"})
    blocked = client.stream(
        "POST",
        "/api/v1/chat/completions/stream",
        headers={"Authorization": "Bearer token-guest-00000001"},
        json=chat_payload(mode="agent"),
    )
    with blocked as response:
        assert response.status_code == 403
        response.read()
    assert provider.calls == []
