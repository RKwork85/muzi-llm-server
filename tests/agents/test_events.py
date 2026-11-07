"""事件队列有界、脱敏，并且不阻塞发布方。"""

from __future__ import annotations

import json

from app.agents.events import EventPublisher
from app.agents.models import AgentEventType


def test_events_are_filtered_and_terminal_survives_a_full_queue() -> None:
    publisher = EventPublisher(request_id="req", task_id="task", max_queue_size=2)
    publisher.publish(
        "agent.step.started",
        {"step": 1, "reasoning_content": "secret-thought", "arguments": '{"expression":"1"}', "tool_name": "calculator"},
    )
    for step in range(2, 8):
        publisher.publish("agent.step.started", {"step": step})
    publisher.publish("agent.completed", {"status": "completed"})
    subscriber = publisher.subscribe()
    items = []
    while not subscriber.empty():
        items.append(subscriber.get_nowait())
    encoded = json.dumps([item.model_dump(mode="json") for item in items], ensure_ascii=False)
    assert "secret-thought" not in encoded
    assert "expression" not in encoded
    assert items[-1].type is AgentEventType.COMPLETED
    assert publisher.dropped > 0
    assert all(item.sequence >= 1 for item in items)


def test_slow_subscriber_does_not_block_publish() -> None:
    publisher = EventPublisher(request_id="req", task_id="task", max_queue_size=1)
    queue = publisher.subscribe()
    publisher.publish("agent.started", {})
    for step in range(3):
        publisher.publish("agent.step.started", {"step": step})
    publisher.publish("agent.failed", {"error_code": "UPSTREAM_TIMEOUT", "status": "failed"})
    assert queue.qsize() <= 1
    assert publisher.dropped > 0
