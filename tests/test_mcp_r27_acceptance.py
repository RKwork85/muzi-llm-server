"""R2.7 和 R2.8 能在本机证明的行为。真实环境和长时压测不在这里标通过。"""

from __future__ import annotations

import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from app.api.health import production_readiness_reason
from app.main import create_app
from app.mcp.deploy_check import REQUIRED_TABLES, database_probe
from app.mcp.execution import MCPExecutionContext, run_mcp
from app.mcp.grants import MemoryGrantStore, bind_grants, stdio_permitted
from app.mcp.keys import KeyConfigurationError, KeyRing, rotate_sealed, validate_key_config
from app.mcp.load_probe import run_probe
from app.mcp.observe import audits, breaker_for, breaker_states, clear_observe, metrics
from app.mcp.protocol import ProtocolError, accept_initialize_result
from app.tools.stdio_access import stdio_access
from tests.conftest import make_settings
from tests.fakes import FakeProvider


@pytest.fixture(autouse=True)
def _clean() -> None:
    clear_observe()
    bind_grants(None)
    yield
    clear_observe()
    bind_grants(None)
    stdio_access.set_catalog(set())


def test_production_key_rules_reject_dev_material_without_logging_it(caplog: pytest.LogCaptureFixture) -> None:
    secret = "p" * 40
    caplog.set_level(logging.INFO)
    with pytest.raises(KeyConfigurationError):
        validate_key_config(
            make_settings(runtime_env="production", mcp_key_source="local", mcp_key_id="prod-1", mcp_token_key=secret)
        )
    with pytest.raises(KeyConfigurationError):
        validate_key_config(
            make_settings(runtime_env="production", mcp_key_source="external", mcp_key_id="local", mcp_token_key=secret)
        )
    with pytest.raises(KeyConfigurationError):
        validate_key_config(
            make_settings(runtime_env="production", mcp_key_source="external", mcp_key_id="prod-1", mcp_token_key="")
        )
    validate_key_config(make_settings(runtime_env="development", mcp_key_source="local", mcp_token_key="short"))
    assert secret not in caplog.text


def test_rotation_keeps_plaintext_and_does_not_replace_unreadable_ciphertext() -> None:
    old = KeyRing(make_settings(mcp_token_key="a" * 32, mcp_key_id="k1"))
    sealed = old.encrypt("access-secret", user_id="user-a", connector_id="demo", credential_type="access_token")
    rotated = KeyRing(
        make_settings(mcp_token_key="b" * 32, mcp_key_id="k2", mcp_previous_token_keys="k1:" + ("a" * 32))
    )
    fresh = rotate_sealed(rotated, sealed, user_id="user-a", connector_id="demo", credential_type="access_token")
    assert fresh.startswith("v2.k2.")
    assert rotated.decrypt(fresh, user_id="user-a", connector_id="demo", credential_type="access_token") == "access-secret"
    with pytest.raises(KeyConfigurationError):
        rotate_sealed(old, fresh, user_id="user-a", connector_id="demo", credential_type="access_token")


def test_production_startup_refuses_memory_grants() -> None:
    app = create_app(
        settings=make_settings(
            runtime_env="production",
            mcp_key_source="external",
            mcp_key_id="prod-1",
            mcp_token_key="k" * 32,
            database_startup_required=False,
        ),
        provider=FakeProvider(),
    )
    with pytest.raises(BaseException) as caught:
        with TestClient(app):
            pass
    assert "内存授权" in repr(caught.value)


def test_development_ready_does_not_require_database(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    reason = production_readiness_reason(
        make_settings(runtime_env="production", mcp_key_source="external", mcp_key_id="prod-1", mcp_token_key="k" * 32),
        database_ready=False,
        grants_durable=False,
    )
    assert reason == "database_unavailable"


@pytest.mark.asyncio
async def test_write_timeout_is_unknown_and_not_retried() -> None:
    calls = {"n": 0}

    async def call() -> str:
        calls["n"] += 1
        await asyncio.sleep(1)
        return "sent"

    ctx = _ctx("write-demo", "write", idempotent=False)
    with pytest.raises(TimeoutError):
        await run_mcp(ctx, _allow, call)
    assert calls["n"] == 1
    assert any(item["status"] == "unknown" for item in audits)
    assert metrics.counters["mcp_retry_total"] == 0


@pytest.mark.asyncio
async def test_read_timeout_retries_once_and_breaker_is_isolated() -> None:
    calls = {"n": 0}

    async def call() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            await asyncio.sleep(1)
        return "ok"

    assert await run_mcp(_ctx("read-demo", "echo", idempotent=True), _allow, call) == "ok"
    assert calls["n"] == 2
    assert metrics.counters["mcp_retry_total"] == 1
    broken = breaker_for("read-demo")
    broken.limit = 1
    broken.failure()
    assert breaker_states["read-demo"] == "open"
    assert breaker_for("other-demo").allow() is True


def test_half_open_allows_one_probe() -> None:
    breaker = breaker_for("half-demo")
    breaker.limit = 1
    breaker.reset_seconds = 0
    breaker.failure()
    assert breaker.allow() is True
    assert breaker.state == "half_open"
    assert breaker.allow() is False
    breaker.failure()
    assert breaker.state == "open"
    breaker.reset_seconds = 0
    breaker.open_until = 0
    assert breaker.allow() is True
    breaker.success()
    assert breaker.state == "closed"


@pytest.mark.asyncio
async def test_cancel_after_write_starts_is_unknown() -> None:
    started = asyncio.Event()

    async def call() -> str:
        started.set()
        await asyncio.sleep(5)
        return "done"

    task = asyncio.create_task(run_mcp(_ctx("cancel-demo", "write", idempotent=False), _allow, call))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert any(item["status"] == "unknown" for item in audits)


@pytest.mark.asyncio
async def test_production_without_store_denies_even_a_memory_grant() -> None:
    stdio_access.set_catalog({"mcp__fake__echo"})
    stdio_access.grant("user-a", {"mcp__fake__echo"})
    bind_grants(None, production=True)
    assert await stdio_permitted("user-a", "mcp__fake__echo") is False


@pytest.mark.asyncio
async def test_revoke_wins_against_concurrent_checks() -> None:
    store = MemoryGrantStore()
    await store.grant_tool("user-a", "demo", "echo")

    async def revoke() -> None:
        await store.revoke_tool("user-a", "demo", "echo")

    await asyncio.gather(revoke(), *(store.permitted("user-a", "demo", "echo") for _ in range(10)))
    assert await store.permitted("user-a", "demo", "echo") is False
    assert await store.permitted("user-b", "demo", "echo") is False


def test_unsupported_protocol_is_rejected() -> None:
    with pytest.raises(ProtocolError):
        accept_initialize_result({"protocolVersion": "2026-07-28", "capabilities": {}, "serverInfo": {"name": "x"}})
    with pytest.raises(ProtocolError):
        accept_initialize_result({"protocolVersion": "2025-11-25-extra", "capabilities": {}})


@pytest.mark.asyncio
async def test_probe_does_not_claim_required_load_tiers() -> None:
    report = await run_probe(concurrency=2, seconds=0.05)
    assert report["production_load"] is False
    assert report["required_tiers"] == [
        {"concurrency": 10, "minutes": 10, "status": "not_run"},
        {"concurrency": 30, "minutes": 30, "status": "not_run"},
        {"concurrency": 50, "minutes": 30, "status": "not_run"},
    ]
    probe = database_probe(make_settings())
    assert probe["status"] in {"unavailable", "unknown"}
    assert list(REQUIRED_TABLES) == list(probe["tables"])
    assert probe["revision_expected"] == "c4a8e2b71f03"


def _ctx(connector: str, tool: str, *, idempotent: bool) -> MCPExecutionContext:
    return MCPExecutionContext(
        user_id="user-a",
        connector_id=connector,
        tool_name=tool,
        local_name=f"mcp__{connector}__{tool}",
        request_id="req",
        timeout=0.05,
        idempotent=idempotent,
    )


async def _allow(item: MCPExecutionContext) -> bool:
    del item
    return True
