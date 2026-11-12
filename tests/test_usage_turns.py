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


def test_conversations_follow_history_and_sum_summary_updates() -> None:
    records = [
        _record(id="a", settlement_key="run-a:1", amount=10),
        _record(id="b", settlement_key="run-b:1", amount=7, created_at=NOW + timedelta(minutes=2)),
    ]
    summaries = [
        {
            "id": "s1",
            "conversation_id": CONV,
            "model": "deepseek-flash",
            "summary_text": "第一次",
            "called_at": NOW,
            "input_tokens": 10,
            "output_tokens": 2,
            "total_tokens": 12,
            "input_breakdown": [
                {
                    "direction": "input",
                    "category": "user",
                    "token_count": 10,
                    "attribution_method": "heuristic_estimated",
                }
            ],
            "output_breakdown": [],
            "cache_breakdown": [],
            "tool_count": 0,
            "call_count": 1,
        },
        {
            "id": "s2",
            "conversation_id": CONV,
            "model": "deepseek-flash",
            "summary_text": "第二次",
            "called_at": NOW + timedelta(minutes=3),
            "input_tokens": 20,
            "output_tokens": 4,
            "total_tokens": 24,
            "input_breakdown": [
                {
                    "direction": "input",
                    "category": "user",
                    "token_count": 20,
                    "attribution_method": "provider_reported",
                }
            ],
            "output_breakdown": [],
            "cache_breakdown": [],
            "tool_count": 1,
            "call_count": 1,
        },
    ]
    sessions = [
        {
            "conversation_id": CONV,
            "title": "你好啊",
            "model_name": "DeepSeek-V4.1-Flash",
            "model_category": "chat",
            "model_api": "chat",
            "provider_id": "deepseek",
            "turn_count": 2,
            "has_images": False,
            "updated_at": NOW,
        }
    ]
    result = _build(
        records,
        [],
        [],
        [],
        [],
        [],
        range_key="all",
        summaries=summaries,
        sessions=sessions,
    )
    assert result["conversation_total"] == 1
    card = result["conversations"][0]
    assert card["title"] == "你好啊"
    assert card["billed_tokens"] == 17
    assert card["turn_count"] == 2
    assert card["provider"] == "DeepSeek"
    assert card["model_kind"] == "文本"
    assert [item["summary"] for item in card["summaries"]] == ["第一次", "第二次"]
    assert len(card["turns"]) == 2
    user = card["summaries"][0]["input_breakdown"][0]
    assert user["label"] == "当前用户输入"
    assert user["figure"]["value"] == 10
    assert user["figure"]["quality"] == "estimated"


def test_kind_filter_keeps_only_the_matching_model_row() -> None:
    records = [
        _record(id="chat", settlement_key="chat:1", amount=5075, metric="chat", source="chat_completion"),
        _record(
            id="image",
            settlement_key="image:1",
            amount=4479,
            metric="image",
            source="image",
            created_at=NOW + timedelta(minutes=1),
            context={"model": "Doubao Seedream 5.0 Flash", "conversation_id": CONV, "conversation_title": "生成一张帅哥图片"},
        ),
    ]
    sessions = [
        {
            "conversation_id": CONV,
            "title": "生成一张帅哥图片",
            "model_name": "DeepSeek-V4.1-Flash",
            "model_category": "chat",
            "model_api": "chat",
            "provider_id": "deepseek",
            "turn_count": 2,
            "has_images": True,
            "updated_at": NOW,
            "model_tracks": [
                {
                    "track": "text",
                    "modelName": "DeepSeek-V4.1-Flash",
                    "providerId": "deepseek",
                    "modelCategory": "chat",
                    "modelApi": "chat",
                    "turns": 1,
                    "tokens": 5075,
                },
                {
                    "track": "image",
                    "modelName": "Doubao Seedream 5.0 Flash",
                    "providerId": "ark",
                    "modelCategory": "image_gen",
                    "modelApi": "images",
                    "turns": 1,
                    "tokens": 4479,
                },
            ],
        }
    ]
    both = _build(records, [], [], [], [], [], range_key="all", sessions=sessions)
    card = both["conversations"][0]
    assert [row["track"] for row in card["tracks"]] == ["text", "image"]

    chat_only = _build(records, [], [], [], [], [], range_key="all", kind="chat", sessions=sessions)
    chat_card = chat_only["conversations"][0]
    assert [row["model"] for row in chat_card["tracks"]] == ["DeepSeek-V4.1-Flash"]
    assert chat_card["tracks"][0]["billed_tokens"] == 5075
    assert all(turn["kind"] == "chat" for turn in chat_card["turns"])

    image_only = _build(records, [], [], [], [], [], range_key="all", kind="image", sessions=sessions)
    image_card = image_only["conversations"][0]
    assert [row["model"] for row in image_card["tracks"]] == ["Doubao Seedream 5.0 Flash"]
    assert image_card["tracks"][0]["billed_tokens"] == 4479
    assert image_card["summaries"] == []
    assert all(turn["kind"] == "image" for turn in image_card["turns"])
