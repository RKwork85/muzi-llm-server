"""工具注册、参数校验和回传。"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from app.core.exceptions import InvalidToolArgumentsError, ToolExecutionError, ToolNotAllowedError
from app.providers.base import ToolCall
from app.services.tool_service import ToolService
from app.tools.calculator import calculate
from app.tools.clock import current_time_handler
from app.tools.clock import EmptyArgs
from app.tools.registry import ToolSpec, build_default_registry
from tests.conftest import make_settings
from tests.fakes import FakeProvider, make_result, make_tool_call


def test_calculator_and_schema() -> None:
    assert calculate("23 * 19") == "437"
    assert calculate("(23 * 19) + 4") == "441"
    registry = build_default_registry()
    schema = registry.get("calculator").openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "calculator"
    assert schema["function"]["parameters"]["type"] == "object"
    assert "expression" in schema["function"]["parameters"]["properties"]
    assert schema["function"]["parameters"]["additionalProperties"] is False
    names = {item.name for item in registry.all_tools()}
    assert names == {"calculator", "get_current_time"}


def test_current_time_format() -> None:
    text = current_time_handler(EmptyArgs())
    assert text.endswith("Z")
    assert "T" in text


def test_source_has_no_dynamic_execution() -> None:
    root = Path(__file__).resolve().parents[1] / "app"
    banned = ("eval(", "exec(", "os.system", "os.popen")
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, f"{path} contains {token}"
        if "subprocess" in text and path.name not in {"sandbox_command.py", "code_runner.py", "mcp_client.py"}:
            raise AssertionError(f"{path} contains subprocess")


@pytest.mark.asyncio
async def test_execute_registered_tool_and_reject_bad_calls() -> None:
    service = ToolService(build_default_registry(), make_settings())
    messages = await service.execute([make_tool_call()])
    assert messages[0]["role"] == "tool"
    assert messages[0]["tool_call_id"] == "call_calc"
    assert "437" in messages[0]["content"]
    assert "server_tool" in messages[0]["content"]

    with pytest.raises(ToolNotAllowedError):
        await service.execute([make_tool_call(name="os_system", arguments="{}")])
    with pytest.raises(InvalidToolArgumentsError):
        await service.execute([make_tool_call(arguments="{bad")])
    with pytest.raises(InvalidToolArgumentsError):
        await service.execute(
            [make_tool_call(arguments='{"expression":"1+1","command":"whoami"}')]
        )
    with pytest.raises(InvalidToolArgumentsError):
        await service.execute([make_tool_call(arguments='{"expression":"2**20"}')])
    with pytest.raises(ToolExecutionError):
        await service.execute([make_tool_call(arguments='{"expression":"1/0"}')])


def test_tool_loop_returns_final_answer(client, provider: FakeProvider) -> None:
    provider.push(
        make_result(
            "",
            tool_calls=[make_tool_call()],
            reasoning="need calc",
            finish_reason="tool_calls",
        )
    )
    provider.push(make_result("23 乘 19 等于 437。", reasoning="hide-me", completion_id="final"))
    response = client.post(
        "/api/v1/chat/tool-calls",
        json={
            "messages": [{"role": "user", "content": "帮我计算 23 * 19"}],
            "prompt_name": "calculator",
            "max_tool_rounds": 3,
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert body["choices"][0]["message"]["content"] == "23 乘 19 等于 437。"
    assert body["tool_rounds"] == 1
    assert "hide-me" not in response.text
    assert "need calc" not in response.text
    follow_up = provider.calls[1].messages
    assistant = follow_up[-2]
    tool_message = follow_up[-1]
    assert assistant["role"] == "assistant"
    assert assistant["reasoning_content"] == "need calc"
    assert assistant["tool_calls"][0]["id"] == "call_calc"
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call_calc"
    assert "437" in tool_message["content"]


def test_unknown_tool_from_model_is_rejected(client, provider: FakeProvider) -> None:
    provider.push(make_result("", tool_calls=[make_tool_call(name="os_system", arguments="{}")]))
    response = client.post(
        "/api/v1/chat/tool-calls",
        json={"messages": [{"role": "user", "content": "运行命令"}], "prompt_name": "default"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "TOOL_NOT_ALLOWED"
    assert response.json()["error"]["message"] == "请求的工具不在允许列表中"
    assert "os_system" not in response.text


def test_round_limit_stops_without_extra_execution(client, provider: FakeProvider) -> None:
    spec = client.app.state.tool_service.registry.get("calculator")
    called = {"n": 0}
    original = spec.handler

    def wrapped(args):
        called["n"] += 1
        return original(args)

    spec.handler = wrapped
    for index in range(3):
        provider.push(
            make_result(
                "",
                tool_calls=[make_tool_call(call_id=f"call_{index}", arguments='{"expression":"1+1"}')],
                finish_reason="tool_calls",
            )
        )
    response = client.post(
        "/api/v1/chat/tool-calls",
        json={
            "messages": [{"role": "user", "content": "一直计算"}],
            "max_tool_rounds": 1,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "TOOL_ROUND_LIMIT"
    assert called["n"] == 1
    assert len(provider.calls) == 2


def test_tool_timeout(client, provider: FakeProvider) -> None:
    spec = client.app.state.tool_service.registry.get("calculator")
    event = threading.Event()

    def hang(_args):
        event.wait(5)
        return "late"

    spec.handler = hang
    provider.push(make_result("", tool_calls=[make_tool_call()], finish_reason="tool_calls"))
    try:
        response = client.post(
            "/api/v1/chat/tool-calls",
            json={"messages": [{"role": "user", "content": "计算 1+1"}]},
        )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "TOOL_EXECUTION_ERROR"
    finally:
        event.set()


def test_client_cannot_select_unregistered_tool_name(client, provider: FakeProvider) -> None:
    response = client.post(
        "/api/v1/chat/tool-calls",
        json={
            "messages": [{"role": "user", "content": "计算"}],
            "tool_names": ["shell"],
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "TOOL_NOT_ALLOWED"
    assert provider.calls == []


def test_high_risk_tool_cannot_be_registered() -> None:
    from pydantic import BaseModel

    class Args(BaseModel):
        pass

    registry = build_default_registry()
    with pytest.raises(ValueError):
        registry.register(
            ToolSpec(
                name="shell",
                description="危险",
                args_model=Args,
                handler=lambda _args: "no",
                risk_level="high",
            )
        )


def test_result_is_truncated_and_stays_json() -> None:
    import json

    settings = make_settings(tool_result_max_chars=96)
    service = ToolService(build_default_registry(), settings)
    content = service._bounded_content("calculator", "x" * 500)
    parsed = json.loads(content)
    assert parsed["source"] == "server_tool"
    assert parsed["name"] == "calculator"
    assert parsed["result"].endswith("...[truncated]")
    assert len(content) <= 96
    assert json.loads(service._bounded_content("calculator", "12"))["result"] == "12"
