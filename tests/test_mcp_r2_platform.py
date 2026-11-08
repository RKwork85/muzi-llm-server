"""R2.2 到 R2.6 的统一执行、授权、加密、观测和并发检查。"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from app.agents.models import AgentRequest
from app.agents.runtime import AgentRuntime
from app.mcp.crypto import TokenCipher
from app.mcp.execution import CURRENT_MCP_SCOPE, MCPExecutionContext, McpScope, context_for, run_mcp
from app.mcp.grants import MemoryGrantStore, bind_grants, stdio_permitted, sync_stdio
from app.mcp.keys import KeyConfigurationError, KeyRing, validate_key_config
from app.mcp.observe import audits, breaker_for, clear_observe, metrics, redact
from app.mcp.service import McpService
from app.mcp.store import MemoryStore
from app.models.mixins import utcnow
from app.services.prompt_service import PromptService
from app.tools.mcp_client import McpArgs
from app.tools.registry import ToolSpec, build_default_registry
from app.tools.stdio_access import stdio_access
from tests.conftest import make_settings
from tests.fakes import FakeProvider, make_result, make_tool_call


@pytest.fixture(autouse=True)
def _reset_observe() -> None:
    clear_observe()
    bind_grants(None)
    yield
    clear_observe()
    bind_grants(None)


def test_context_comes_from_server_scope_not_tool_name_fragments() -> None:
    token = CURRENT_MCP_SCOPE.set(McpScope(user_id="user-a", request_id="req-1", conversation_id="c1"))
    try:
        ctx = context_for("mcp__demo__echo", timeout=2, idempotent=True)
        assert ctx is not None
        assert ctx.user_id == "user-a"
        assert ctx.connector_id == "demo"
        assert ctx.tool_name == "echo"
        assert context_for("mcp__demo", timeout=2, idempotent=True) is None
    finally:
        CURRENT_MCP_SCOPE.reset(token)


@pytest.mark.asyncio
async def test_forged_connector_and_write_are_not_retried() -> None:
    calls = {"n": 0}

    async def call() -> str:
        calls["n"] += 1
        raise RuntimeError("write failed")

    ctx = MCPExecutionContext(
        user_id="user-a",
        connector_id="demo",
        tool_name="write",
        local_name="mcp__other__write",
        request_id="req",
        timeout=1,
        idempotent=False,
    )

    async def allowed(item: MCPExecutionContext) -> bool:
        return item.connector_id == "demo" and item.local_name == "mcp__demo__write"

    with pytest.raises(PermissionError):
        await run_mcp(ctx, allowed, call)
    assert calls["n"] == 0

    ctx = MCPExecutionContext(
        user_id="user-a",
        connector_id="demo",
        tool_name="write",
        local_name="mcp__demo__write",
        request_id="req",
        timeout=1,
        idempotent=False,
    )
    with pytest.raises(RuntimeError):
        await run_mcp(ctx, allowed, call)
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_idempotent_read_retries_once() -> None:
    calls = {"n": 0}

    async def call() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("temporary")
        return "ok"

    ctx = MCPExecutionContext(
        user_id="user-a",
        connector_id="retry-demo",
        tool_name="echo",
        local_name="mcp__retry-demo__echo",
        request_id="req",
        timeout=1,
        idempotent=True,
    )

    async def allowed(item: MCPExecutionContext) -> bool:
        del item
        return True

    assert await run_mcp(ctx, allowed, call) == "ok"
    assert calls["n"] == 2
    assert metrics.counters["mcp_tool_calls_total"] == 2
    assert metrics.samples["mcp_tool_call_duration"]
    assert audits[-1]["status"] == "success"
    assert "token" not in audits[-1]


@pytest.mark.asyncio
async def test_breaker_opens_after_failures() -> None:
    breaker_for("closed-demo").limit = 1

    async def call() -> str:
        raise RuntimeError("down")

    ctx = MCPExecutionContext(
        user_id="user-a",
        connector_id="closed-demo",
        tool_name="echo",
        local_name="mcp__closed-demo__echo",
        request_id="req",
        timeout=1,
        idempotent=False,
    )

    async def allowed(item: MCPExecutionContext) -> bool:
        del item
        return True

    with pytest.raises(RuntimeError):
        await run_mcp(ctx, allowed, call)
    with pytest.raises(PermissionError):
        await run_mcp(ctx, allowed, call)
    assert metrics.counters["mcp_auth_denied_total"] == 1


@pytest.mark.asyncio
async def test_grants_are_per_user_and_survive_a_restart_snapshot() -> None:
    store = MemoryGrantStore()
    await store.grant_tool("user-a", "fake", "echo")
    await store.grant_tool("user-b", "fake", "other")
    await asyncio.gather(
        store.grant_tool("user-a", "fake", "echo"),
        store.revoke_tool("user-b", "fake", "other"),
    )
    assert await store.permitted("user-a", "fake", "echo") is True
    assert await store.permitted("user-a", "fake", "other") is False
    assert await store.permitted("user-b", "fake", "other") is False
    await store.revoke_connector("user-a", "fake")
    assert await store.permitted("user-a", "fake", "echo") is False
    restored = MemoryGrantStore.restore(store.snapshot())
    assert await restored.permitted("user-a", "fake", "echo") is False
    assert restored.audits[-1].action == "revoke_connector"


@pytest.mark.asyncio
async def test_connector_grant_does_not_allow_every_tool_and_expiry_denies() -> None:
    store = MemoryGrantStore()
    await store.ensure_default("user-a", "fake", set())
    assert await store.permitted("user-a", "fake", "echo") is False
    await store.grant_tool("user-a", "fake", "echo", expires_at=utcnow() - timedelta(seconds=1))
    assert await store.permitted("user-a", "fake", "echo") is False


@pytest.mark.asyncio
async def test_stdio_reload_keeps_revocation_after_catalog_reset() -> None:
    store = MemoryGrantStore()
    stdio_access.set_catalog({"mcp__fake__echo"})
    bind_grants(store)
    await sync_stdio("user-a", "fake")
    assert stdio_access.allows("user-a", "mcp__fake__echo") is True
    await store.revoke_tool("user-a", "fake", "echo")
    stdio_access.set_catalog({"mcp__fake__echo"})
    assert stdio_access.allows("user-a", "mcp__fake__echo") is False
    await sync_stdio("user-a", "fake")
    assert stdio_access.allows("user-a", "mcp__fake__echo") is False
    assert await stdio_permitted("user-b", "mcp__fake__echo") is False
    stdio_access.set_catalog(set())


@pytest.mark.asyncio
async def test_ciphertext_is_bound_and_legacy_token_still_opens() -> None:
    settings = make_settings(mcp_token_key="test-token-key-012345", mcp_key_id="local")
    ring = KeyRing(settings)
    sealed = ring.encrypt("access-secret", user_id="user-a", connector_id="demo", credential_type="access_token")
    assert "access-secret" not in sealed
    assert ring.decrypt(sealed, user_id="user-a", connector_id="demo", credential_type="access_token") == "access-secret"
    with pytest.raises(KeyConfigurationError):
        ring.decrypt(sealed, user_id="user-b", connector_id="demo", credential_type="access_token")
    rotated = KeyRing(
        make_settings(
            mcp_token_key="another-token-key-32-characters",
            mcp_key_id="k2",
            mcp_previous_token_keys="local:test-token-key-012345",
        )
    )
    assert rotated.decrypt(sealed, user_id="user-a", connector_id="demo", credential_type="access_token") == "access-secret"
    legacy = TokenCipher("test-token-key-012345").encrypt("plain-token")
    client = _unused_client()
    service = McpService(settings, MemoryStore(), client)
    try:
        assert service._open_access(legacy, "user-a", "demo")["token"] == "plain-token"
    finally:
        await service.aclose()
        await client.aclose()
    validate_key_config(
        make_settings(
            runtime_env="production",
            mcp_key_source="external",
            mcp_key_id="prod-1",
            mcp_token_key="x" * 32,
        )
    )
    with pytest.raises(KeyConfigurationError):
        validate_key_config(make_settings(runtime_env="production", mcp_key_source="local", mcp_token_key="x" * 32))


def test_logs_drop_secrets() -> None:
    clean = redact({"access_token": "secret", "user_id": "user-a", "note": "Bearer secret"})
    assert "access_token" not in clean
    assert clean["user_id"] == "user-a"
    assert clean["note"] == "[redacted]"


@pytest.mark.asyncio
async def test_agent_runtime_runs_remote_tool_for_the_authenticated_user() -> None:
    seen: list[str] = []

    class Remote:
        async def tool_specs(self, user_id: str) -> list[ToolSpec]:
            if user_id != "user-a":
                return []

            async def handler(args: McpArgs) -> str:
                del args
                scope = CURRENT_MCP_SCOPE.get()
                assert scope is not None and scope.user_id == "user-a"
                seen.append(scope.user_id)
                return "remote-ok"

            return [
                ToolSpec(
                    name="mcp__demo__echo",
                    description="remote echo",
                    args_model=McpArgs,
                    handler=handler,
                    access="read_network",
                    requires_network=True,
                    toolset="mcp",
                )
            ]

    provider = FakeProvider()
    provider.push(make_result("", tool_calls=[make_tool_call("call-1", "mcp__demo__echo", "{}")]))
    provider.push(make_result("done"))
    runtime = AgentRuntime(
        settings=make_settings(tool_timeout_seconds=2),
        provider=provider,
        prompts=PromptService(),
        registry=build_default_registry(),
    )
    try:
        view = await runtime.start(
            AgentRequest(message="调用远程工具", prompt_name="calculator"),
            "req-remote",
            mcp_user_id="user-a",
            mcp_service=Remote(),
        )
        status = await _wait(runtime, str(view["task_id"]))
        assert status["status"] == "completed"
        assert seen == ["user-a"]
        provider.push(make_result("no-tool"))
        other = await runtime.start(
            AgentRequest(message="不要调用", prompt_name="calculator"),
            "req-other",
            mcp_user_id="user-b",
            mcp_service=Remote(),
        )
        await _wait(runtime, str(other["task_id"]))
        matched = [call for call in provider.calls if "不要调用" in str(call.messages)]
        assert matched
        names = [item["function"]["name"] for item in matched[0].tools or []]
        assert "mcp__demo__echo" not in names
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_many_users_do_not_share_grants() -> None:
    store = MemoryGrantStore()

    async def grant(index: int) -> None:
        await store.grant_tool(f"user-{index}", "demo", "echo")

    await asyncio.gather(*(grant(index) for index in range(20)))
    for index in range(20):
        assert await store.permitted(f"user-{index}", "demo", "echo") is True
        assert await store.permitted(f"user-{index}", "demo", "write") is False


def test_grant_migration_follows_the_connection_revision() -> None:
    from pathlib import Path

    path = Path(__file__).parents[1] / "alembic" / "versions" / "c4a8e2b71f03_mcp_authorization_grants.py"
    text = path.read_text(encoding="utf-8")
    assert 'down_revision: Union[str, Sequence[str], None] = "b7e1c0a94d11"' in text
    assert "mcp_connector_grants" in text
    assert "mcp_tool_grants" in text
    assert "mcp_authorization_audits" in text
    assert "def downgrade" in text


def _unused_client() -> object:
    import httpx

    return httpx.AsyncClient()


async def _wait(runtime: AgentRuntime, task_id: str) -> dict[str, object]:
    for _ in range(50):
        view = runtime.get(task_id)
        if view["status"] in {"completed", "failed", "cancelled", "limit_reached"}:
            return view
        await asyncio.sleep(0.02)
    return runtime.get(task_id)
