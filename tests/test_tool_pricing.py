"""工具成本换算。Search/Fetch 每 1000 次成功请求 = 5 元。"""

from __future__ import annotations

from app.metering.tool_pricing import get_token_price, quote_tool_money, quote_tool_tokens
from app.tools.registry import ToolPricing


def _external() -> ToolPricing:
    return ToolPricing(
        model="external",
        external_price="5",
        external_unit=1000,
        currency="CNY",
        requests_per_call=1,
    )


def test_quote_money_success() -> None:
    assert quote_tool_money(_external(), success=True) is not None
    assert float(quote_tool_money(_external(), success=True)) == 0.005


def test_quote_money_failure_is_none() -> None:
    assert quote_tool_money(_external(), success=False) is None


def test_quote_tokens_success() -> None:
    # 5 / 1000 * 1 / 0.000001 = 5000 token
    assert quote_tool_tokens(_external(), success=True) == 5000


def test_quote_tokens_failure_is_zero() -> None:
    assert quote_tool_tokens(_external(), success=False) == 0


def test_free_tool_costs_nothing() -> None:
    assert quote_tool_tokens(ToolPricing(), success=True) == 0
    assert quote_tool_money(ToolPricing(), success=True) is None
    assert quote_tool_tokens(None, success=True) == 0


def test_fixed_tokens() -> None:
    pricing = ToolPricing(model="fixed_tokens", fixed_tokens=120)
    assert quote_tool_tokens(pricing, success=True) == 120


def test_fixed_tokens_failure_is_zero() -> None:
    pricing = ToolPricing(model="fixed_tokens", fixed_tokens=120)
    assert quote_tool_tokens(pricing, success=False) == 0


def test_token_price_from_config(monkeypatch) -> None:
    from app.core import config

    monkeypatch.setattr(config, "get_settings", lambda: _FakeSettings("0.00001"))
    assert get_token_price() is not None


class _FakeSettings:
    def __init__(self, value: str) -> None:
        self.token_price_cny = value
