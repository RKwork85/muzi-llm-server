"""充值入账不得附带补发角色默认额度。

回归背景：管理端「删除用户额度信息」会删掉 usage_quotas 整行。此前的
`credit_tokens` 走 `ensure_default_quota`，会先补发一份角色默认额度
（member 为 10 万），导致 1.00 元买 100 万 token 的用户到账 110 万。
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest

from app.authorization.application.audit_service import DEFAULT_QUOTA_KEY, QuotaService
from app.authorization.application.authorization_service import AuthorizationService
from app.models.rbac import QuotaAdjustment, UsageQuota


class ScriptedSession:
    """按顺序返回预设结果的 Session 替身：不连库，只验证写入逻辑。"""

    def __init__(self, scalars: list[Any] | None = None) -> None:
        self._scalars = list(scalars or [])
        self.added: list[Any] = []
        self.flushes = 0

    async def scalar(self, _stmt: Any) -> Any:
        if self._scalars:
            return self._scalars.pop(0)
        # 回读（with_for_update）时返回本次刚建的行。
        return self.added[-1] if self.added else None

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushes += 1

    async def refresh(self, _obj: Any) -> None:
        return None

    @asynccontextmanager
    async def begin_nested(self):
        yield


def _fake_roles(monkeypatch: pytest.MonkeyPatch, roles: list[str]) -> None:
    async def get_user_roles(self, user_id):  # noqa: ANN001, ARG001
        return roles

    monkeypatch.setattr(AuthorizationService, "get_user_roles", get_user_roles)


def _quotas(session: ScriptedSession) -> list[UsageQuota]:
    return [obj for obj in session.added if isinstance(obj, UsageQuota)]


def _adjustments(session: ScriptedSession) -> list[QuotaAdjustment]:
    return [obj for obj in session.added if isinstance(obj, QuotaAdjustment)]


async def test_credit_without_quota_row_adds_exactly_paid_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有额度行时（被删除过），入账必须是精确 token，不能叠加角色默认额度。"""
    _fake_roles(monkeypatch, ["member"])
    session = ScriptedSession(scalars=[None, None])  # 1) reference 无记录 2) 无额度行
    user_id = uuid.uuid4()

    quota = await QuotaService(session).credit_tokens(
        user_id, 1_000_000, reference="order-1"
    )

    assert quota.limit_value == 1_000_000
    assert quota.limit_source == "manual"
    created = _quotas(session)
    assert len(created) == 1
    adjustments = _adjustments(session)
    assert len(adjustments) == 1
    assert adjustments[0].before_limit == 0
    assert adjustments[0].after_limit == 1_000_000
    assert adjustments[0].unit_size == 1_000_000


async def test_credit_keeps_existing_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """已有额度行时按原额度累加，行为不变。"""
    _fake_roles(monkeypatch, ["member"])
    existing = UsageQuota(
        user_id=uuid.uuid4(),
        quota_key=DEFAULT_QUOTA_KEY,
        period="total",
        limit_value=500_000,
        used_value=120_000,
        limit_source="manual",
        display_value=500_000,
    )
    session = ScriptedSession(scalars=[None, existing, existing])

    quota = await QuotaService(session).credit_tokens(
        existing.user_id, 1_000_000, reference="order-2"
    )

    assert quota.limit_value == 1_500_000
    assert _adjustments(session)[0].before_limit == 500_000


async def test_credit_skips_unlimited_super_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    """无额度行的超级管理员建为不限量，且不写充值流水。"""
    _fake_roles(monkeypatch, ["super_admin"])
    session = ScriptedSession(scalars=[None, None])

    quota = await QuotaService(session).credit_tokens(
        uuid.uuid4(), 1_000_000, reference="order-3"
    )

    assert quota.limit_source == "unlimited"
    assert quota.limit_value == 0
    assert _adjustments(session) == []


async def test_ensure_paid_quota_never_grants_role_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_roles(monkeypatch, ["member"])
    session = ScriptedSession(scalars=[None])

    quota = await QuotaService(session).ensure_paid_quota(uuid.uuid4())

    assert quota.limit_value == 0
    assert quota.limit_source == "manual"


async def test_is_unlimited_falls_back_to_role(monkeypatch: pytest.MonkeyPatch) -> None:
    """额度行被删后，超级管理员仍须被判定为不限量，否则付费后拿不到额度。"""
    unlimited_row = UsageQuota(
        user_id=uuid.uuid4(),
        quota_key=DEFAULT_QUOTA_KEY,
        period="total",
        limit_value=0,
        limit_source="unlimited",
        display_value=0,
    )
    assert await QuotaService(ScriptedSession(scalars=[unlimited_row])).is_unlimited(
        unlimited_row.user_id
    )

    _fake_roles(monkeypatch, ["super_admin"])
    assert await QuotaService(ScriptedSession(scalars=[None])).is_unlimited(uuid.uuid4())

    _fake_roles(monkeypatch, ["member"])
    assert not await QuotaService(ScriptedSession(scalars=[None])).is_unlimited(uuid.uuid4())
