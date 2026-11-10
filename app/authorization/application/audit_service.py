"""审计与额度服务。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import QuotaExceededError
from app.models.rbac import AccessAuditLog, UsageQuota, UsageRecord


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        actor_id: uuid.UUID | str | None,
        action: str,
        *,
        subject_id: uuid.UUID | str | None = None,
        permission: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        decision: str | None = None,
        outcome: str = "success",
        context: dict[str, Any] | None = None,
    ) -> AccessAuditLog:
        log = AccessAuditLog(
            actor_id=uuid.UUID(str(actor_id)) if actor_id is not None else None,
            subject_id=uuid.UUID(str(subject_id)) if subject_id is not None else None,
            action=action,
            permission=permission,
            resource_type=resource_type,
            resource_id=resource_id,
            decision=decision,
            outcome=outcome,
            context_json=context or {},
        )
        self.session.add(log)
        await self.session.flush()
        return log

    async def list_logs(
        self,
        *,
        actor_id: uuid.UUID | str | None = None,
        subject_id: uuid.UUID | str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AccessAuditLog]:
        query = select(AccessAuditLog).order_by(AccessAuditLog.created_at.desc())
        if actor_id is not None:
            query = query.where(AccessAuditLog.actor_id == actor_id)
        if subject_id is not None:
            query = query.where(AccessAuditLog.subject_id == subject_id)
        rows = await self.session.scalars(query.limit(limit).offset(offset))
        return list(rows)


class QuotaService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_quotas(self, user_id: uuid.UUID | str) -> list[UsageQuota]:
        rows = await self.session.scalars(
            select(UsageQuota).where(UsageQuota.user_id == user_id).order_by(UsageQuota.quota_key)
        )
        return list(rows)

    async def get_quota(
        self, user_id: uuid.UUID | str, quota_key: str, period: str = "total"
    ) -> UsageQuota | None:
        return await self.session.scalar(
            select(UsageQuota).where(
                UsageQuota.user_id == user_id,
                UsageQuota.quota_key == quota_key,
                UsageQuota.period == period,
            )
        )

    async def set_quota(
        self,
        user_id: uuid.UUID | str,
        quota_key: str,
        limit_value: int,
        period: str = "total",
    ) -> UsageQuota:
        quota = await self.get_quota(user_id, quota_key, period)
        if quota is None:
            quota = UsageQuota(
                user_id=user_id, quota_key=quota_key, period=period, limit_value=limit_value
            )
            self.session.add(quota)
        else:
            quota.limit_value = limit_value
        await self.session.flush()
        return quota

    async def check(self, user_id: uuid.UUID | str, quota_key: str) -> bool:
        quota = await self.get_quota(user_id, quota_key)
        if quota is None or quota.limit_value <= 0:
            return True  # 未设置配额视为不限
        return quota.used_value < quota.limit_value

    async def consume(
        self,
        user_id: uuid.UUID | str,
        quota_key: str,
        amount: int = 1,
        metric: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """原子自增已用额度。达到上限抛 QuotaExceededError。"""
        if amount <= 0:
            return
        quota = await self.get_quota(user_id, quota_key)
        if quota is not None and quota.limit_value > 0:
            if quota.used_value + amount > quota.limit_value:
                raise QuotaExceededError()
            quota.used_value += amount
        self.session.add(
            UsageRecord(
                user_id=user_id,
                metric=metric or quota_key,
                amount=amount,
                context_json=context or {},
            )
        )
        await self.session.flush()
