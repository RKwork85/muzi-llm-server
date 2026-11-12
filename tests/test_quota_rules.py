"""额度判断不连接数据库。"""

from __future__ import annotations

from app.authorization.application.audit_service import quota_allows, settlement_result


def test_zero_limit_blocks_and_unlimited_allows() -> None:
    assert quota_allows("manual", 0, 0) is False
    assert quota_allows("unlimited", 0, 10_000) is True
    assert quota_allows("role_default", 100, 100) is False
    assert quota_allows("role_default", 100, 99) is True


def test_settlement_records_overage_and_missing_usage() -> None:
    assert settlement_result("role_default", 100, 130, 40) == "exceeded"
    assert settlement_result("role_default", 100, 80, 20) == "settled"
    assert settlement_result("unlimited", 0, 20, 20) == "settled"
    assert settlement_result("role_default", 100, 50, 0) == "unsettled"
