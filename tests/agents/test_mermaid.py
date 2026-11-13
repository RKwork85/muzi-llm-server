"""Mermaid 与本地工具。全部离线。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.tools.mermaid import (
    FlowEdge,
    FlowNode,
    FlowchartArgs,
    SequenceArgs,
    ValidateArgs,
    escape_mermaid,
    render_flowchart,
    render_sequence,
    validate_mermaid,
)
from app.tools.local_tools import DateDiffArgs, FormatJsonArgs, TextStatsArgs, date_diff, format_json, text_stats
from app.tools.registry import build_agent_registry, build_default_registry
from tests.conftest import make_settings
from tests.fakes import FakeProvider, make_result, make_tool_call
from tests.support_agent_access import AGENT_HEADERS, allow_legacy_agent_api


@pytest.fixture(autouse=True)
def _legacy_agent_api(monkeypatch) -> None:
    allow_legacy_agent_api(monkeypatch)


def _flow(**overrides: object) -> FlowchartArgs:
    data: dict[str, object] = {
        "nodes": [
            {"key": "start", "label": "开始"},
            {"key": "end", "label": '说 "hi" <tag> & 更多'},
        ],
        "edges": [{"source": "start", "target": "end", "label": "是|否; # `"}],
    }
    data.update(overrides)
    return FlowchartArgs.model_validate(data)


def test_flowchart_is_byte_stable_and_escapes() -> None:
    source = render_flowchart(_flow())
    assert source == render_flowchart(_flow())
    assert source.startswith("flowchart TB\n")
    assert 'n1["开始"]' in source
    assert "n2[" in source
    assert "#quot;" in source
    assert "#lt;" in source
    assert "#amp;" in source
    assert "#124;" in source
    assert "#59;" in source
    assert "#35;" in source
    assert "#96;" in source
    assert "click" not in source
    assert "classDef" not in source
    assert 'end["' not in source
    assert escape_mermaid('"<>&#;|`') == "#quot;#lt;#gt;#amp;#35;#59;#124;#96;"


def test_internal_ids_cover_reserved_and_unicode_keys() -> None:
    args = FlowchartArgs.model_validate(
        {
            "nodes": [
                {"key": "end", "label": "结束"},
                {"key": "graph", "label": "图"},
                {"key": "class", "label": "类"},
                {"key": "开始", "label": "中文"},
                {"key": "😀", "label": "表情"},
            ],
            "edges": [
                {"source": "end", "target": "开始"},
                {"source": "😀", "target": "class"},
            ],
        }
    )
    source = render_flowchart(args)
    assert "n1[" in source
    assert "n4[" in source
    assert "n5[" in source
    assert "\n  end" not in source
    assert "😀" not in source


def test_flowchart_rejects_bad_structure() -> None:
    with pytest.raises(ValueError):
        FlowchartArgs.model_validate(
            {
                "nodes": [{"key": "a", "label": "A"}, {"key": "a", "label": "B"}],
                "edges": [],
            }
        )
    with pytest.raises(ValueError):
        FlowchartArgs.model_validate(
            {
                "nodes": [{"key": "a", "label": "A"}],
                "edges": [{"source": "a", "target": "missing"}],
            }
        )
    with pytest.raises(ValueError):
        FlowchartArgs.model_validate(
            {"nodes": [{"key": "a", "label": "A", "extra": 1}], "edges": []}
        )


@pytest.mark.asyncio
async def test_sequence_and_validate_do_not_promise_render() -> None:
    args = SequenceArgs.model_validate(
        {
            "participants": [{"key": "user", "label": "用户"}, {"key": "api", "label": "接口"}],
            "messages": [
                {"source": "user", "target": "api", "text": "你好; #"},
                {"source": "api", "target": "user", "text": "收到", "kind": "reply"},
                {"source": "user", "target": "api", "text": "稍后", "kind": "async"},
            ],
        }
    )
    source = render_sequence(args)
    assert source == render_sequence(args)
    assert source.startswith("sequenceDiagram\n")
    assert "p1->>p2:" in source
    assert "p2-->>p1:" in source
    assert "p1-)p2:" in source
    assert "#59;" in source
    checked = json.loads(await validate_mermaid(ValidateArgs(source="flowchart TB\n  n1[\"ok\"]\n")))
    assert checked["valid"] is True
    assert checked["guarantees_render"] is False
    registry = build_agent_registry()
    assert "不保证可渲染" in registry.get("validate_mermaid").description
    for name, category in (
        ("%%{init: {}}", "config"),
        ("---\nflowchart TB", "config"),
        ("flowchart TB\nclick n1 href \"javascript:alert(1)\"", "interactive"),
        ("flowchart TB\n  n1[\"<script>alert(1)</script>\"]", "html"),
        ("pie title x", "unknown_type"),
    ):
        body = json.loads(await validate_mermaid(ValidateArgs(source=name)))
        assert body["valid"] is False
        assert body["guarantees_render"] is False
        assert category in body["issues"]
        assert "<script>" not in json.dumps(body["issues"])


@pytest.mark.asyncio
async def test_local_tools_are_pure() -> None:
    formatted = json.loads(await format_json(FormatJsonArgs(text='{"b":1,"a":2}')))
    assert formatted["formatted"] == '{\n  "a": 2,\n  "b": 1\n}'
    stats = json.loads(await text_stats(TextStatsArgs(text="a b\nc")))
    assert stats["words"] == 3
    assert stats["lines"] == 2
    diff = json.loads(
        await date_diff(DateDiffArgs(start="2026-11-06T00:00:00Z", end="2026-11-07T01:02:03Z"))
    )
    assert diff["timezone"] == "UTC"
    assert diff["days"] == 1
    assert diff["total_seconds"] == 86400 + 3600 + 120 + 3
    with pytest.raises(ValueError):
        await format_json(FormatJsonArgs(text="[" * 40 + "]"))


def test_default_registry_stays_phase1() -> None:
    assert {item.name for item in build_default_registry().all_tools()} == {
        "calculator",
        "get_current_time",
    }
    names = {item.name for item in build_agent_registry().all_tools()}
    assert "create_flowchart" in names
    assert "format_json" in names


def test_diagram_artifact_round_trip(provider: FakeProvider) -> None:
    provider.push(
        make_result(
            content=None,
            tool_calls=[
                make_tool_call(
                    "call_flow",
                    "create_flowchart",
                    json.dumps(
                        {
                            "nodes": [
                                {"key": "start", "label": "开始"},
                                {"key": "end", "label": "结束"},
                            ],
                            "edges": [{"source": "start", "target": "end"}],
                        }
                    ),
                )
            ],
            finish_reason="tool_calls",
        )
    )
    provider.push(make_result("图已生成"))
    app = create_app(settings=make_settings(), provider=provider)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/agents/runs",
            headers=AGENT_HEADERS,
            json={"message": "画登录流程", "prompt_name": "default", "toolsets": ["diagram"]},
        )
        assert created.status_code == 200
        task_id = created.json()["task_id"]
        finished = None
        for _ in range(40):
            response = client.get(f"/api/v1/agents/runs/{task_id}", headers=AGENT_HEADERS)
            body = response.json()
            if body["status"] in {"completed", "failed", "cancelled", "limit_reached"}:
                finished = body
                break
        assert finished is not None
        assert finished["status"] == "completed"
        assert finished["answer"] == "图已生成"
        assert finished["artifacts"][0]["type"] == "mermaid"
        assert finished["artifacts"][0]["content"].startswith("flowchart TB\n")
        assert "n1[" in finished["artifacts"][0]["content"]
        with client.stream("GET", f"/api/v1/agents/runs/{task_id}/events", headers=AGENT_HEADERS) as stream:
            raw = stream.read().decode("utf-8")
    assert "artifact_type" in raw
    assert "flowchart TB" not in raw


def test_default_request_cannot_call_diagram(provider: FakeProvider) -> None:
    provider.push(
        make_result(
            content=None,
            tool_calls=[make_tool_call("call_flow", "create_flowchart", '{"nodes":[{"key":"a","label":"A"}]}')],
            finish_reason="tool_calls",
        )
    )
    provider.push(make_result("没有图"))
    app = create_app(settings=make_settings(), provider=provider)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/agents/runs",
            headers=AGENT_HEADERS,
            json={"message": "画图", "prompt_name": "default"},
        )
        task_id = created.json()["task_id"]
        finished = None
        for _ in range(40):
            body = client.get(f"/api/v1/agents/runs/{task_id}", headers=AGENT_HEADERS).json()
            if body["status"] in {"completed", "failed", "cancelled", "limit_reached"}:
                finished = body
                break
    assert finished is not None
    assert finished["status"] == "completed"
    assert "artifacts" not in finished
    offered = provider.calls[0].tools or []
    names = {item["function"]["name"] for item in offered}
    assert names == {"calculator", "get_current_time"}
