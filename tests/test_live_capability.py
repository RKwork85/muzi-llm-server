"""能力归属实测。默认跳过。DEEPSEEK_LIVE=1 时对照组和实验组各运行 3 次。"""

from __future__ import annotations

import json
import os
import re
import time

import pytest
from fastapi.testclient import TestClient

from tests.test_live_deepseek import _assert_no_secret, _scrub, _settings

pytestmark = pytest.mark.skipif(
    os.environ.get("DEEPSEEK_LIVE") != "1",
    reason="设置 DEEPSEEK_LIVE=1 才会请求真实模型",
)

_MESSAGE = "画一个用户登录流程图"
_TERMINAL = {"completed", "failed", "cancelled", "limit_reached"}


def test_capability_attribution_three_each() -> None:
    settings = _settings(tool_timeout_seconds=10, max_tool_rounds=3, log_level="WARNING")
    from app.main import create_app

    app = create_app(settings=settings)
    rows: list[dict[str, object]] = []
    with TestClient(app) as client:
        for group, toolsets in (
            ("control", ["basic"]),
            ("experiment", ["basic", "diagram"]),
        ):
            for index in range(1, 4):
                rows.append(_one(client, group, index, toolsets))
    print("CAPABILITY " + json.dumps(rows, ensure_ascii=False))
    _assert_no_secret(json.dumps(rows, ensure_ascii=False))
    assert [row["group"] for row in rows].count("control") == 3
    assert [row["group"] for row in rows].count("experiment") == 3
    assert all(row["status"] in _TERMINAL for row in rows)


def _one(client: TestClient, group: str, index: int, toolsets: list[str]) -> dict[str, object]:
    created = client.post(
        "/api/v1/agents/runs",
        json={"message": _MESSAGE, "prompt_name": "default", "toolsets": toolsets},
    )
    assert created.status_code == 200
    task_id = created.json()["task_id"]
    finished = None
    for _ in range(80):
        body = client.get(f"/api/v1/agents/runs/{task_id}").json()
        if body["status"] in _TERMINAL:
            finished = body
            break
        time.sleep(0.5)
    assert finished is not None
    events = _event_text(client, task_id)
    tools = sorted(set(re.findall(r'"tool_name":"([^"]+)"', events)))
    artifacts = finished.get("artifacts") or []
    answer = finished.get("answer") or ""
    return {
        "group": group,
        "index": index,
        "status": finished["status"],
        "error_code": finished.get("error_code"),
        "tool_calls": finished.get("tool_calls"),
        "tool_names": tools,
        "artifact_types": [item.get("type") for item in artifacts],
        "artifact_sources": [item.get("source_tool") for item in artifacts],
        "answer_has_mermaid": "```" in answer or "flowchart" in answer.lower() or "mermaid" in answer.lower(),
        "answer_excerpt": _scrub(answer.replace("\n", " ")[:120]),
    }


def _event_text(client: TestClient, task_id: str) -> str:
    chunks: list[str] = []
    with client.stream("GET", f"/api/v1/agents/runs/{task_id}/events") as response:
        assert response.status_code == 200
        for chunk in response.iter_text():
            chunks.append(chunk)
            joined = "".join(chunks)
            if any(name in joined for name in ("agent.completed", "agent.failed", "agent.limit_reached", "agent.cancelled")):
                break
    text = _scrub("".join(chunks))
    _assert_no_secret(text)
    return text
