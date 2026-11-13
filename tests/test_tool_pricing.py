"""工具固定计费。系统内部只认 token，free 不扣、fixed_tokens 成功才扣。"""

from __future__ import annotations

from app.metering.tool_pricing import quote_tool_tokens, resolve_tokens
from app.tools.registry import ToolPricing


def test_free_tool_costs_nothing() -> None:
    assert quote_tool_tokens(ToolPricing(), success=True) == 0
    assert quote_tool_tokens(None, success=True) == 0


def test_fixed_tokens_success() -> None:
    pricing = ToolPricing(model="fixed_tokens", tokens=120)
    assert quote_tool_tokens(pricing, success=True) == 120


def test_fixed_tokens_failure_is_zero() -> None:
    pricing = ToolPricing(model="fixed_tokens", tokens=120)
    assert quote_tool_tokens(pricing, success=False) == 0


def test_fixed_tokens_with_tool_name() -> None:
    pricing = ToolPricing(model="fixed_tokens", tokens=120)
    assert quote_tool_tokens(pricing, tool_name="deliver_file", success=True) == 120


def test_fixed_tokens_env_override(monkeypatch) -> None:
    monkeypatch.setenv("TOOL_PRICE_DELIVER_FILE_TOKENS", "9999")
    pricing = ToolPricing(model="fixed_tokens", tokens=120)
    assert quote_tool_tokens(pricing, tool_name="deliver_file", success=True) == 9999


def test_image_unit_bills_per_image(monkeypatch) -> None:
    monkeypatch.delenv("TOOL_PRICE_IMAGE_GENERATION_TOKENS", raising=False)
    pricing = ToolPricing(model="fixed_tokens", tokens=150000, unit="image")
    assert quote_tool_tokens(pricing, tool_name="image_generation", success=True, units=3) == 450000


def test_image_unit_zero_units_is_zero() -> None:
    pricing = ToolPricing(model="fixed_tokens", tokens=150000, unit="image")
    assert quote_tool_tokens(pricing, success=True, units=0) == 0


def test_resolve_tokens_fallback() -> None:
    assert resolve_tokens("no_such_tool", 2000) == 2000
    assert resolve_tokens("", 7) == 7
