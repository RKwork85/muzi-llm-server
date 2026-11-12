"""轮次聚合只读：同一轮合并，缓存不重复计入，估算不改账单。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.authorization.application.usage_turns import build_usage_analysis
from app.schemas.usage_analysis import UsageAnalysisResponse

NOW = datetime(2026, 11, 11, 6, 30, tzinfo=timezone.utc)
CONV = "11111111-1111-1111-1111-111111111111"


def _record(**overrides):
    context = {
        "model": "DeepSeek-V4.1-Flash",
        "conversation_id": CONV,
        "conversation_title": "你好啊",
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }
    context.update(overrides.pop("context", {}))
    payload = {
        "id": "usage-1",
        "metric": "chat",
        "amount": 0,
        "source": "chat_completion",
        "settlement_key": "run:1",
        "result": "settled",
        "created_at": NOW,
        "context": context,
    }
    payload.update(overrides)
    return payload


def _llm(**overrides):
    payload = {
        "id": "llm-1",
        "settlement_key": "run:1",
        "request_id": "req-plain",
        "provider": "deepseek",
        "model": "DeepSeek-V4.1-Flash",
        "runtime_mode": "ask",
        "input_tokens": 100,
        "output_tokens": 10,
        "reasoning_tokens": None,
        "cache_read_tokens": 40,
        "cache_write_tokens": None,
        "total_tokens": 110,
        "usage_source": "chat",
        "status": "completed",
        "started_at": NOW,
        "completed_at": NOW + timedelta(seconds=2),
        "created_at": NOW,
        "breakdown": [],
    }
    payload.update(overrides)
    return payload


def _build(*args, **kwargs):
    result = build_usage_analysis(*args, **kwargs)
    UsageAnalysisResponse.model_validate(result)
    return result


def test_same_run_groups_and_same_title_does_not() -> None:
    records = [
        _record(id="a", settlement_key="run-a:1", amount=10, context={"prompt_tokens": 8, "completion_tokens": 2}),
        _record(
            id="b",
            settlement_key="run-a:2",
            amount=5,
            created_at=NOW + timedelta(seconds=3),
            context={"prompt_tokens": 4, "completion_tokens": 1},
        ),
        _record(
            id="c",
            settlement_key="run-b:1",
            amount=7,
            created_at=NOW + timedelta(minutes=2),
            context={"prompt_tokens": 6, "completion_tokens": 1, "conversation_title": "你好啊"},
        ),
    ]
    result = _build(records, [], [], [], [], [], range_key="all")
    assert result["total"] == 2
    grouped = next(item for item in result["items"] if item["billed_tokens"]["value"] == 15)
    assert grouped["call_count"] == 2
    assert grouped["input_tokens"]["value"] == 12
    assert grouped["output_tokens"]["value"] == 3


def test_summary_attaches_to_latest_turn_without_using_text() -> None:
    records = [
        _record(id="chat", settlement_key="run:1", amount=100, context={"prompt_tokens": 80, "completion_tokens": 20}),
        _record(
            id="summary",
            source="summary",
            settlement_key="summary-key",
            amount=20,
            created_at=NOW + timedelta(minutes=1),
            context={"purpose": "summary", "prompt_tokens": 15, "completion_tokens": 5, "conversation_title": "你好啊"},
        ),
    ]
    result = _build(records, [], [], [], [], [], range_key="all")
    assert result["total"] == 1
    turn = result["items"][0]
    assert turn["billed_tokens"]["value"] == 120
    assert turn["summary_count"] == 1
    assert turn["link"] == "conversation_latest_turn"
    assert turn["input_tokens"]["value"] == 95
    assert turn["output_tokens"]["value"] == 25


def test_late_summary_stays_separate() -> None:
    records = [
        _record(id="chat", settlement_key="run:1", amount=100, context={"prompt_tokens": 80, "completion_tokens": 20}),
        _record(
            id="summary",
            source="summary",
            settlement_key="summary-key",
            amount=20,
            created_at=NOW + timedelta(hours=7),
            context={"purpose": "summary", "prompt_tokens": 15, "completion_tokens": 5},
        ),
    ]
    result = _build(records, [], [], [], [], [], range_key="all")
    assert result["total"] == 2
    assert any(item["link"] == "unlinked" for item in result["items"])


def test_cache_is_not_counted_twice_and_estimate_is_not_the_bill() -> None:
    records = [
        _record(id="usage-1", settlement_key="run:1", amount=110, context={"prompt_tokens": 100, "completion_tokens": 10}),
    ]
    llm = _llm(
        breakdown=[
            {
                "direction": "input",
                "category": "system",
                "token_count": 50,
                "attribution_method": "heuristic_estimated",
            }
        ]
    )
    result = _build(records, [llm], [], [], [], [], range_key="all")
    composition = result["composition"]
    assert composition["cache_split"] is True
    assert composition["fresh_input"]["value"] == 60
    assert composition["cache_read"]["value"] == 40
    assert composition["output"]["value"] == 10
    assert composition["total"]["value"] == 110
    assert composition["billed_total"] == 110
    assert composition["fresh_input"]["value"] + composition["cache_read"]["value"] + composition["output"]["value"] == 110
    turn = result["items"][0]
    system = next(row for row in turn["input_breakdown"] if row["category"] == "system")
    assert system["figure"] == {"value": 50, "quality": "estimated"}
    assert turn["billed_tokens"] == {"value": 110, "quality": "actual"}
    assert system["figure"]["value"] != turn["billed_tokens"]["value"]


def test_identifiers_are_masked() -> None:
    records = [
        _record(
            id="usage-1",
            settlement_key="settle_SECRETVALUE_0001",
            amount=110,
            context={"prompt_tokens": 100, "completion_tokens": 10},
        )
    ]
    llm = _llm(
        settlement_key="settle_SECRETVALUE_0001",
        request_id="req_SECRETVALUE_zzzz",
    )
    result = _build(records, [llm], [], [], [], [], range_key="all")
    blob = UsageAnalysisResponse.model_validate(result).model_dump_json()
    assert "SECRETVALUE" not in blob
    technical = result["items"][0]["technical"]
    assert technical["request_ids_masked"]
    assert technical["pricing_version"] == "未提供"
