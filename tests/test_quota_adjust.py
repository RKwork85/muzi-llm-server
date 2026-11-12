"""额度按 10 万调整的计算。不连接数据库。"""

from __future__ import annotations

import pytest

from app.authorization.application.audit_service import resolve_quota_limit
from app.core.exceptions import InvalidRequestError


def test_increase_uses_100k_units() -> None:
    assert resolve_quota_limit(100_000, "increase", 2) == 300_000


def test_decrease_stops_at_zero() -> None:
    assert resolve_quota_limit(250_000, "decrease", 1) == 150_000
    with pytest.raises(InvalidRequestError):
        resolve_quota_limit(100_000, "decrease", 2)


def test_reset_replaces_the_limit() -> None:
    assert resolve_quota_limit(100_000, "reset", 5) == 500_000
    assert resolve_quota_limit(100_000, "reset", 0) == 0


def test_unlimited_only_allows_reset() -> None:
    assert resolve_quota_limit(0, "reset", 1, unlimited=True) == 100_000
    with pytest.raises(InvalidRequestError):
        resolve_quota_limit(0, "increase", 1, unlimited=True)


def test_increase_requires_a_unit() -> None:
    with pytest.raises(InvalidRequestError):
        resolve_quota_limit(100_000, "increase", 0)
