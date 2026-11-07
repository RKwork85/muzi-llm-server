"""T0：工具元数据、toolset、调用上限、网络闸门和 artifact。"""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel, ConfigDict

from app.agents.artifacts import absorb_tool_output
from app.agents.context import ContextManager
from app.agents.events import EventPublisher
from app.agents.executor import ToolExecutor
from app.agents.loop import AgentLoop
from app.agents.models import TaskStatus
from app.agents.policy import PolicyError, RunClock, default_server_policy, server_policy_from_settings
from app.agents.toolsets import resolve_tool_names
from app.tools.registry import ToolRegistry, ToolSpec, build_default_registry
from tests.conftest import make_settings
from tests.fakes import FakeProvider, make_result, make_tool_call


class TextArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = "ok"


def test_existing_tools_keep_metadata_defaults() -> None:
    spec = build_default_registry().get("calculator")
    assert spec is not None
    assert spec.risk_level == "low"
    assert spec.access == "read_local"
    assert spec.requires_network is False
    assert spec.toolset == "basic"
    assert spec.idempotent is True
    assert spec.max_calls_per_run is None
    policy = default_server_policy(build_default_registry())
    assert policy.allowed_tools == ("calculator", "get_current_time")


def test_write_access_is_rejected_and_high_risk_stays_rejected() -> None:
    registry = ToolRegistry()

    def handler(_: TextArgs) -> str:
        return "no"

    with pytest.raises(ValueError):
        registry.register(
            ToolSpec("shell", "危险", TextArgs, handler, risk_level="low", access="write_local")
        )
    with pytest.raises(ValueError):
        registry.register(ToolSpec("shell", "危险", TextArgs, handler, risk_level="high"))


def test_unknown_and_disabled_toolsets() -> None:
    registry = build_default_registry()
    settings = make_settings()
    with pytest.raises(PolicyError) as unknown:
        resolve_tool_names(registry, settings, ["mcp"])
    assert unknown.value.code == "TOOLSET_UNKNOWN"
    with pytest.raises(PolicyError) as offline:
        resolve_tool_names(registry, settings, ["basic", "research"])
    assert offline.value.code == "TOOLSET_UNAVAILABLE"
    assert resolve_tool_names(registry, settings, None) == ("calculator", "get_current_time")
    enabled = make_settings(tools_network_enabled=True)
    assert resolve_tool_names(registry, enabled, ["research"]) == ()


def test_workspace_root_cannot_be_the_project_root(tmp_path) -> None:
    registry = build_default_registry()
    blocked = make_settings(tools_workspace_root=".")
    with pytest.raises(PolicyError) as caught:
        resolve_tool_names(registry, blocked, ["workspace"])
    assert caught.value.code == "TOOLSET_UNAVAILABLE"
    opened = make_settings(tools_workspace_root=str(tmp_path))
    assert resolve_tool_names(registry, opened, ["workspace"]) == ()


def test_artifact_summary_drops_body_and_enforces_count() -> None:
    envelope = json.dumps(
        {
            "source": "server_tool",
            "name": "create_flowchart",
            "result": json.dumps(
                {
                    "diagram_type": "flowchart",
                    "artifacts": [
                        {
                            "type": "mermaid",
                            "title": "登录",
                            "content": "flowchart TB\n  n1[\"开始\"]",
                            "content_type": "text/vnd.mermaid",
                            "source_tool": "create_flowchart",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        },
        ensure_ascii=False,
    )
    content, artifacts, error = absorb_tool_output(envelope, already=0, max_count=5, max_chars=20000)
    assert error is None
    assert artifacts[0]["id"] == "art_1"
    assert "flowchart TB" not in content
    assert "art_1" in content
    again, rejected, limit = absorb_tool_output(envelope, already=5, max_count=5, max_chars=20000)
    assert rejected == []
    assert limit == "ARTIFACT_LIMIT"
    assert "flowchart TB" not in again


def test_event_keeps_artifact_size_not_body() -> None:
    publisher = EventPublisher(request_id="req", task_id="task", max_queue_size=4)
    publisher.publish(
        "agent.tool.completed",
        {
            "tool_name": "create_flowchart",
            "artifact_id": "art_1",
            "artifact_type": "mermaid",
            "artifact_size": 12,
            "artifact_count": 1,
            "content": "flowchart TB\n  n1[\"secret\"]",
        },
    )
    queue = publisher.subscribe()
    event = queue.get_nowait()
    assert event.data["artifact_id"] == "art_1"
    assert event.data["artifact_size"] == 12
    assert "content" not in event.data
    assert "secret" not in json.dumps(event.data)


@pytest.mark.asyncio
async def test_per_tool_call_limit_returns_json_failure() -> None:
    registry = build_default_registry()

    def echo(_: TextArgs) -> str:
        return "once"

    registry.register(
        ToolSpec(
            name="echo",
            description="回声",
            args_model=TextArgs,
            handler=echo,
            max_calls_per_run=1,
        )
    )
    settings = make_settings(tool_timeout_seconds=2, deepseek_timeout_seconds=5)
    policy = server_policy_from_settings(settings, registry).model_copy(
        update={"allowed_tools": ("calculator", "echo", "get_current_time"), "max_tool_calls": 4}
    )
    provider = FakeProvider()
    provider.push(
        make_result(
            content=None,
            tool_calls=[make_tool_call("call_1", "echo", "{}"), make_tool_call("call_2", "echo", "{}")],
            finish_reason="tool_calls",
        )
    )
    provider.push(make_result("完成"))
    loop = AgentLoop(
        provider=provider,
        executor=ToolExecutor(registry, policy.allowed_tools),
        context=ContextManager(system_prompt="系统", user_message="hi", policy=policy),
        policy=policy,
        clock=RunClock.start(policy),
        registry=registry,
        model="deepseek-flash",
        thinking="disabled",
        request_id="req-limit",
    )
    result = await loop.run()
    assert result.status is TaskStatus.COMPLETED
    tool_messages = [item for item in loop._context.public_messages() if item.get("role") == "tool"]
    assert len(tool_messages) == 2
    assert json.loads(str(tool_messages[1]["content"]))["error"]["code"] == "TOOL_CALL_LIMIT"


@pytest.mark.asyncio
async def test_async_network_tool_does_not_use_sync_pool() -> None:
    class Pool:
        def __init__(self) -> None:
            self.submits = 0

        def submit(self, *_args: object, **_kwargs: object) -> None:
            self.submits += 1
            raise AssertionError("async tool entered the sync pool")

    async def fetch(_: TextArgs) -> str:
        return "page"

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="web_search",
            description="搜索",
            args_model=TextArgs,
            handler=fetch,
            access="read_network",
            requires_network=True,
            toolset="research",
        )
    )
    policy = server_policy_from_settings(
        make_settings(tool_timeout_seconds=2),
        build_default_registry(),
    ).model_copy(update={"allowed_tools": ("web_search",)})
    executor = ToolExecutor(registry, policy.allowed_tools)
    pool = Pool()
    executor.bind_sync_executor(pool)
    executor.bind_network_gate(asyncio.Semaphore(1))
    result = await executor.execute(
        tool_call_id="call_web",
        tool_name="web_search",
        arguments="{}",
        allowlist=policy.allowed_tools,
        clock=RunClock.start(policy),
    )
    assert result.error_code is None
    assert pool.submits == 0


@pytest.mark.asyncio
async def test_network_queue_time_can_expire_the_budget() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def hold(_: TextArgs) -> str:
        started.set()
        await release.wait()
        return "late"

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="web_search",
            description="搜索",
            args_model=TextArgs,
            handler=hold,
            access="read_network",
            requires_network=True,
            toolset="research",
            timeout_s=2,
        )
    )
    policy = server_policy_from_settings(
        make_settings(tool_timeout_seconds=2, deepseek_timeout_seconds=5),
        build_default_registry(),
    ).model_copy(update={"allowed_tools": ("web_search",), "tool_timeout_seconds": 2})
    executor = ToolExecutor(registry, policy.allowed_tools)
    executor.bind_network_gate(asyncio.Semaphore(1))
    first = asyncio.create_task(
        executor.execute(
            tool_call_id="call_hold",
            tool_name="web_search",
            arguments="{}",
            allowlist=("web_search",),
            clock=RunClock.start(policy),
        )
    )
    await started.wait()
    short = policy.model_copy(update={"tool_timeout_seconds": 0.05, "max_wall_time_seconds": 30})
    second = await executor.execute(
        tool_call_id="call_wait",
        tool_name="web_search",
        arguments="{}",
        allowlist=("web_search",),
        clock=RunClock.start(short),
    )
    assert second.status.value == "timed_out"
    assert "late" not in second.content
    release.set()
    finished = await first
    assert finished.error_code is None
