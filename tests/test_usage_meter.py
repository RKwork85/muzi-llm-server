"""用量分类：权威值与估算分开，分类互斥。"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.metering.attribute import annotate_provider_usage, build_meter, estimate_tokens, message_segments
from app.metering.pricing import quote_cost
from app.providers.base import ToolCall, UsageStats
from app.providers.parsing import parse_usage


def test_estimate_counts_cjk_as_one_token() -> None:
    assert estimate_tokens("好") == 1
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("") == 0


def test_history_and_current_user_are_exclusive() -> None:
    messages = [
        {"role": "system", "content": "系统"},
        {"role": "user", "content": "上一问"},
        {"role": "assistant", "content": "上一答"},
        {"role": "tool", "content": "工具结果"},
        {"role": "user", "content": "这一问"},
    ]
    segments = message_segments(messages, skip_system=False)
    categories = [item[0] for item in segments if item[0]]
    assert categories == ["system", "history", "history", "tool_result", "user"]
    texts = [item[1] for item in segments if item[0]]
    assert texts.count("工具结果") == 1


def test_provider_remainder_is_other_input_not_added_into_system() -> None:
    meter = build_meter(
        provider_name="deepseek",
        model="deepseek-flash",
        runtime_mode="ask",
        usage_source="chat",
        status="completed",
        usage=UsageStats(prompt_tokens=100, completion_tokens=2, total_tokens=102),
        messages=[{"role": "system", "content": "系统提示"}, {"role": "user", "content": "好"}],
        tools=None,
        system_text="系统提示",
        developer_text="",
        skill_text="",
        skill_refs=None,
        output_text="好",
        tool_calls=[],
        structured=False,
    )
    assert meter["input_tokens"] == 100
    assert meter["output_tokens"] == 2
    assert meter["total_tokens"] == 102
    system = [row for row in meter["breakdown"] if row["category"] == "system"]
    other = [row for row in meter["breakdown"] if row["category"] == "other_input"]
    assert system and system[0]["attribution_method"] == "heuristic_estimated"
    assert other and other[0]["metadata"]["reason"] == "provider_remainder"
    estimated = sum(
        row["token_count"]
        for row in meter["breakdown"]
        if row["direction"] == "input" and row["token_count"] is not None
    )
    assert estimated == 100


def test_cache_is_not_added_into_input_total() -> None:
    meter = build_meter(
        provider_name="deepseek",
        model="deepseek-flash",
        runtime_mode="ask",
        usage_source="chat",
        status="completed",
        usage=UsageStats(
            prompt_tokens=10,
            completion_tokens=1,
            total_tokens=11,
            cache_read_tokens=4,
        ),
        messages=[{"role": "user", "content": "好"}],
        tools=None,
        system_text="",
        developer_text="",
        skill_text="",
        skill_refs=None,
        output_text="好",
        tool_calls=[],
        structured=False,
    )
    cache = [row for row in meter["breakdown"] if row["direction"] == "cache"]
    assert {row["category"] for row in cache} == {"cache_read", "uncached_input"}
    billed = meter["input_tokens"] + meter["output_tokens"]
    assert billed == meter["total_tokens"]
    assert meter["cache_read_tokens"] == 4


def test_reasoning_tokens_are_not_invented_from_text() -> None:
    meter = build_meter(
        provider_name="deepseek",
        model="deepseek-flash",
        runtime_mode="ask",
        usage_source="chat",
        status="completed",
        usage=UsageStats(prompt_tokens=3, completion_tokens=1, total_tokens=4),
        messages=[{"role": "user", "content": "好"}],
        tools=None,
        system_text="",
        developer_text="",
        skill_text="",
        skill_refs=None,
        output_text="好",
        tool_calls=[],
        structured=False,
    )
    assert meter["reasoning_tokens"] is None
    assert not any(row["category"] == "reasoning" for row in meter["breakdown"])


def test_provider_reasoning_is_marked_actual() -> None:
    meter = build_meter(
        provider_name="deepseek",
        model="deepseek-flash",
        runtime_mode="ask",
        usage_source="chat",
        status="completed",
        usage=UsageStats(prompt_tokens=3, completion_tokens=5, total_tokens=8, reasoning_tokens=4),
        messages=[{"role": "user", "content": "好"}],
        tools=None,
        system_text="",
        developer_text="",
        skill_text="",
        skill_refs=None,
        output_text="好",
        tool_calls=[ToolCall(id="call_1", name="web_search", arguments='{"q":"x"}')],
        structured=False,
    )
    reasoning = next(row for row in meter["breakdown"] if row["category"] == "reasoning")
    assert reasoning["token_count"] == 4
    assert reasoning["attribution_method"] == "provider_reported"
    assert any(row["category"] == "tool_call" for row in meter["breakdown"])


def test_tool_usage_is_grouped_by_tool_name() -> None:
    """工具调用按工具名聚合，token 一律记 0（不再字符估算，定价由 resource 事件下发）。"""
    from app.metering.attribute import tool_usage_rows

    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "write_docx", "arguments": '{"title":"x"}'}},
                {"id": "c2", "type": "function", "function": {"name": "deliver_file", "arguments": '{"path":"a"}'}},
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "doc created ok"},
        {"role": "tool", "tool_call_id": "c2", "content": "sent"},
    ]
    calls = [
        ToolCall(id="c1", name="write_docx", arguments='{"title":"x"}'),
        ToolCall(id="c2", name="deliver_file", arguments='{"path":"a"}'),
    ]
    rows = tool_usage_rows(messages, calls)
    by_name = {row["tool_name"]: row for row in rows}
    assert set(by_name) == {"write_docx", "deliver_file"}
    assert by_name["write_docx"]["calls"] == 1
    assert by_name["deliver_file"]["calls"] == 1
    assert by_name["write_docx"]["tokens"] == 0
    assert by_name["deliver_file"]["tokens"] == 0


def test_tool_usage_in_meter_survives_build() -> None:
    """build_meter 返回 tool_usage 字段，供前端工具行展示；token 一律记 0。"""
    meter = build_meter(
        provider_name="deepseek",
        model="deepseek-flash",
        runtime_mode="ask",
        usage_source="chat",
        status="completed",
        usage=UsageStats(prompt_tokens=10, completion_tokens=4, total_tokens=14),
        messages=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "write_docx", "arguments": '{"title":"x"}'}},
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        ],
        tools=None,
        system_text="",
        developer_text="",
        skill_text="",
        skill_refs=None,
        output_text="",
        tool_calls=[ToolCall(id="c1", name="write_docx", arguments='{"title":"x"}')],
        structured=False,
    )
    assert meter["tool_usage"] == [
        {"tool_name": "write_docx", "calls": 1, "tokens": 0}
    ]


def test_parse_usage_keeps_missing_details_empty() -> None:
    usage = parse_usage(
        {
            "prompt_tokens": 365,
            "completion_tokens": 36,
            "total_tokens": 401,
            "prompt_tokens_details": {"cached_tokens": 12},
            "completion_tokens_details": {"reasoning_tokens": 0},
        }
    )
    assert usage is not None
    assert usage.cache_read_tokens == 12
    assert usage.reasoning_tokens == 0
    assert usage.cache_write_tokens is None


def test_quote_without_price_is_not_a_bill() -> None:
    cost, discount, currency = quote_cost(
        None,
        input_tokens=10,
        output_tokens=2,
        reasoning_tokens=None,
        cache_read_tokens=None,
        cache_write_tokens=None,
    )
    assert cost is None and discount is None and currency is None


def test_quote_uses_decimal_and_does_not_bill_cache_twice() -> None:
    pricing = SimpleNamespace(
        input_price=Decimal("1"),
        output_price=Decimal("2"),
        reasoning_price=Decimal("3"),
        cache_read_price=Decimal("0.1"),
        cache_write_price=None,
        price_unit=1,
        currency="USD",
    )
    cost, discount, currency = quote_cost(
        pricing,
        input_tokens=10,
        output_tokens=5,
        reasoning_tokens=2,
        cache_read_tokens=4,
        cache_write_tokens=None,
    )
    # 未缓存 6 * 1 + 缓存读 4 * 0.1 + 回答 3 * 2 + 思考 2 * 3
    assert cost == Decimal("6") + Decimal("0.4") + Decimal("6") + Decimal("6")
    assert discount == Decimal("3.6")
    assert currency == "USD"
    assert isinstance(cost, Decimal)


def test_provider_total_without_categories_is_not_left_blank() -> None:
    public, meter = annotate_provider_usage(
        provider_name="ark",
        model="doubao-seed-2-1-lite",
        messages=[{"role": "user", "content": "你好"}],
        usage_raw={"prompt_tokens": 136, "completion_tokens": 1112, "total_tokens": 1248},
        output_text="好的",
        usage_source="ark_chat",
    )
    assert public["prompt_tokens"] == 136
    assert public["completion_tokens"] == 1112
    assert public["total_tokens"] == 1248
    assert "reasoning_tokens" not in public
    categories = {row["category"] for row in public["breakdown"]}
    assert "user" in categories
    assert "other_input" in categories
    assert "answer" in categories
    assert "reasoning" not in categories
    assert meter["input_tokens"] == 136
    assert meter["output_tokens"] == 1112
