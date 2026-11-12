"""Agent 额度在步骤结束前结算。"""

from __future__ import annotations

from app.agents.models import AgentEventType, EventEnvelope
from app.authorization.application.quota_bridge import quota_event_observer


async def test_agent_step_settles_before_drain(monkeypatch) -> None:
    settled: list[tuple[object, int, str]] = []

    async def fake_settle(user_id, **kwargs):
        settled.append((user_id, kwargs["amount"], kwargs["settlement_key"]))
        return None

    monkeypatch.setattr(
        "app.authorization.application.quota_bridge.settle_in_new_session",
        fake_settle,
    )
    observer = quota_event_observer(None, "user-1", "task-9")
    observer(
        EventEnvelope(
            request_id="req-1",
            task_id="task-9",
            sequence=1,
            timestamp="2026-11-11T00:00:00Z",
            type=AgentEventType.MODEL_COMPLETED,
            data={"step": 2, "prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
        )
    )
    assert settled == []
    await observer.drain()
    assert settled == [("user-1", 12, "task-9:2")]
