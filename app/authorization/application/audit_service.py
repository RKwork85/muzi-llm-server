"""审计与额度服务。"""

from __future__ import annotations

import uuid
from typing import Any

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InvalidRequestError, QuotaExceededError
from app.models.mixins import utcnow
from app.models.rbac import AccessAuditLog, QuotaAdjustment, UsageQuota, UsageRecord

# 默认额度 key（total_tokens）。
DEFAULT_QUOTA_KEY = "total_tokens"
LIMIT_SOURCES = ("role_default", "manual", "unlimited")
QUOTA_UNIT = 100_000
QUOTA_ACTIONS = ("increase", "decrease", "reset")


def resolve_quota_limit(
    current: int,
    action: str,
    units: int,
    *,
    unlimited: bool = False,
    unit: int = QUOTA_UNIT,
) -> int:
    """按 10 万为单位计算调整后的上限。"""
    if action not in QUOTA_ACTIONS:
        raise InvalidRequestError("不支持的调整方式")
    if units < 0:
        raise InvalidRequestError("数值不能为负")
    if unlimited and action != "reset":
        raise InvalidRequestError("不限量额度只能重置为具体额度")
    if action != "reset" and units < 1:
        raise InvalidRequestError("增加或减少至少填写 1 个单位")
    delta = units * unit
    if action == "increase":
        return current + delta
    if action == "decrease":
        if delta > current:
            raise InvalidRequestError("减少后额度不能小于 0")
        return current - delta
    return delta


def quota_allows(limit_source: str, limit_value: int, used_value: int) -> bool:
    """不限量放行。限额为 0 或已用达到上限时拒绝。"""
    if limit_source == "unlimited":
        return True
    if limit_value <= 0:
        return False
    return used_value < limit_value


def settlement_result(limit_source: str, limit_value: int, used_after: int, amount: int) -> str:
    """结算结果。缺用量不增加已用；超过上限仍然入账。"""
    if amount <= 0:
        return "unsettled"
    if limit_source == "unlimited":
        return "settled"
    if limit_value <= 0 or used_after > limit_value:
        return "exceeded"
    return "settled"


@dataclass(frozen=True)
class Settlement:
    status: str
    amount: int
    used_value: int
    limit_value: int
    unlimited: bool


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

    async def ensure_default_quota(self, user_id: uuid.UUID | str) -> UsageQuota | None:
        """按用户角色补发默认额度（幂等）。

        - 超级管理员：强制不限量（limit_value=0），每次登录都会校准。
        - 其他角色：首次补发对应角色默认额度；已有额度记录时不改动（尊重手动调整）。

        返回创建/已存在的额度行。
        """
        from app.authorization.application.authorization_service import AuthorizationService
        from app.authorization.domain.catalog import SUPER_ADMIN_ROLE_CODE, default_quota_for_roles

        roles = await AuthorizationService(self.session).get_user_roles(user_id)
        quota = await self.get_quota(user_id, DEFAULT_QUOTA_KEY)

        if SUPER_ADMIN_ROLE_CODE in roles:
            if quota is None:
                quota = UsageQuota(
                    user_id=user_id,
                    quota_key=DEFAULT_QUOTA_KEY,
                    period="total",
                    limit_value=0,
                    limit_source="unlimited",
                    display_value=0,
                )
                self.session.add(quota)
            else:
                quota.limit_value = 0
                quota.display_value = 0
                quota.limit_source = "unlimited"
            await self.session.flush()
            return quota

        limit = default_quota_for_roles(roles)
        if limit <= 0:
            return quota
        if quota is None:
            quota = UsageQuota(
                user_id=user_id,
                quota_key=DEFAULT_QUOTA_KEY,
                period="total",
                limit_value=limit,
                limit_source="role_default",
                display_value=limit,
            )
            self.session.add(quota)
            await self.session.flush()
        return quota

    async def sync_quota_to_role(self, user_id: uuid.UUID | str) -> UsageQuota | None:
        """角色变更后校准额度。

        - 超级管理员：强制不限量。
        - 其他角色：仅当当前额度仍是「某内置角色的默认值」（未被手动调整）时，
          才升级/降级到新角色的默认额度；手动调整过的值保持不变。
        """
        from app.authorization.application.authorization_service import AuthorizationService
        from app.authorization.domain.catalog import SUPER_ADMIN_ROLE_CODE, default_quota_for_roles

        roles = await AuthorizationService(self.session).get_user_roles(user_id)
        quota = await self.get_quota(user_id, DEFAULT_QUOTA_KEY)

        if SUPER_ADMIN_ROLE_CODE in roles:
            if quota is None:
                quota = UsageQuota(
                    user_id=user_id,
                    quota_key=DEFAULT_QUOTA_KEY,
                    period="total",
                    limit_value=0,
                    limit_source="unlimited",
                    display_value=0,
                )
                self.session.add(quota)
            else:
                quota.limit_value = 0
                quota.display_value = 0
                quota.limit_source = "unlimited"
            await self.session.flush()
            return quota

        target = default_quota_for_roles(roles)
        if quota is None:
            if target > 0:
                quota = UsageQuota(
                    user_id=user_id,
                    quota_key=DEFAULT_QUOTA_KEY,
                    period="total",
                    limit_value=target,
                    limit_source="role_default",
                    display_value=target,
                )
                self.session.add(quota)
                await self.session.flush()
            return quota

        if quota.limit_source == "unlimited":
            quota.limit_value = target
            quota.display_value = target
            quota.limit_source = "role_default"
            await self.session.flush()
            return quota
        if quota.limit_source == "role_default" and target > 0:
            quota.limit_value = target
            quota.display_value = target
            await self.session.flush()
        return quota

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
        limit_source: str = "manual",
    ) -> UsageQuota:
        if limit_source not in LIMIT_SOURCES:
            limit_source = "manual"
        quota = await self.get_quota(user_id, quota_key, period)
        if quota is None:
            quota = UsageQuota(
                user_id=user_id,
                quota_key=quota_key,
                period=period,
                limit_value=limit_value,
                limit_source=limit_source,
                display_value=limit_value,
            )
            self.session.add(quota)
        else:
            quota.limit_value = limit_value
            quota.display_value = limit_value
            quota.limit_source = limit_source
        await self.session.flush()
        return quota

    async def adjust_quota(
        self,
        user_id: uuid.UUID | str,
        actor_id: uuid.UUID | str,
        *,
        action: str,
        units: int,
        reason: str,
        quota_key: str = DEFAULT_QUOTA_KEY,
        period: str = "total",
    ) -> UsageQuota:
        """按 10 万为单位增加、减少或重置额度，并写入调整流水。"""
        text = reason.strip()
        if not text:
            raise InvalidRequestError("请填写调整原因")
        if len(text) > 200:
            raise InvalidRequestError("调整原因不能超过 200 字")
        quota = await self.get_quota(user_id, quota_key, period)
        unlimited = quota is not None and quota.limit_source == "unlimited"
        current = 0 if quota is None or unlimited else quota.limit_value
        used = 0 if quota is None else quota.used_value
        after = resolve_quota_limit(current, action, units, unlimited=unlimited)
        if quota is None:
            quota = UsageQuota(
                user_id=user_id,
                quota_key=quota_key,
                period=period,
                limit_value=after,
                used_value=0,
                limit_source="manual",
                display_value=after,
            )
            self.session.add(quota)
        else:
            quota.limit_value = after
            quota.display_value = after
            quota.limit_source = "manual"
        quota.adjust_reason = text
        quota.adjusted_by = actor_id
        quota.adjusted_at = utcnow()
        self.session.add(
            QuotaAdjustment(
                user_id=user_id,
                actor_id=actor_id,
                quota_key=quota_key,
                period=period,
                action=action,
                units=units,
                unit_size=QUOTA_UNIT,
                before_limit=current,
                after_limit=after,
                before_used=used,
                reason=text,
            )
        )
        await self.session.flush()
        return quota

    async def check(self, user_id: uuid.UUID | str, quota_key: str) -> bool:
        quota = await self.get_quota(user_id, quota_key)
        if quota is None:
            return False
        return quota_allows(quota.limit_source, quota.limit_value, quota.used_value)

    async def authorize(self, user_id: uuid.UUID | str) -> UsageQuota:
        """调用模型前检查。没有额度行时先补角色默认额度，仍没有则拒绝。"""
        quota = await self.ensure_default_quota(user_id)
        if quota is None:
            quota = await self.get_quota(user_id, DEFAULT_QUOTA_KEY)
        if quota is None or not quota_allows(quota.limit_source, quota.limit_value, quota.used_value):
            raise QuotaExceededError()
        return quota

    async def consume(
        self,
        user_id: uuid.UUID | str,
        quota_key: str,
        amount: int = 1,
        metric: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> Settlement:
        """计入 total_tokens。超过上限仍然入账，由下一次 authorize 拒绝。"""
        return await self.settle(
            user_id,
            source="consume",
            settlement_key=str(uuid.uuid4()),
            amount=amount,
            kind=metric or quota_key,
            extra=context,
            quota_key=quota_key,
        )

    async def get_quota_summary(self, user_id: uuid.UUID | str) -> dict[str, Any]:
        """返回用户额度摘要（总量/已用/剩余），供个人中心展示。"""
        quota = await self.get_quota(user_id, DEFAULT_QUOTA_KEY)
        if quota is None:
            return {
                "quota_key": DEFAULT_QUOTA_KEY,
                "limit_value": 0,
                "used_value": 0,
                "remaining": 0,
                "unlimited": False,
                "limit_source": "manual",
            }
        unlimited = quota.limit_source == "unlimited"
        limit = quota.limit_value
        used = quota.used_value
        return {
            "quota_key": quota.quota_key,
            "limit_value": limit,
            "used_value": used,
            "remaining": 0 if unlimited else max(0, limit - used),
            "unlimited": unlimited,
            "limit_source": quota.limit_source,
        }

    async def record_consumption(
        self,
        user_id: uuid.UUID | str,
        *,
        kind: str,
        amount: int,
        model: str | None = None,
        conversation_id: str | None = None,
        conversation_title: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        image_count: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> UsageRecord:
        """记录一次额度消耗并扣减 total_tokens 额度。

        - kind: chat（对话）/ image（图片生成）
        - 超限时抛 QuotaExceededError，但调用方（如聊天流）可选择忽略以不阻断主流程。
        """
        context: dict[str, Any] = {
            "kind": kind,
            "model": model,
            "conversation_id": conversation_id,
            "conversation_title": conversation_title,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }
        if image_count is not None:
            context["image_count"] = image_count
        if extra:
            context.update(extra)
        settlement_key = str(context.pop("settlement_key", "") or uuid.uuid4())
        source = str(context.pop("source", kind) or kind)
        await self.settle(
            user_id,
            source=source,
            settlement_key=settlement_key,
            amount=amount,
            kind=kind,
            model=model,
            conversation_id=conversation_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            image_count=image_count,
            extra=context,
        )
        found = await self.session.scalar(
            select(UsageRecord).where(
                UsageRecord.user_id == user_id,
                UsageRecord.source == source,
                UsageRecord.settlement_key == settlement_key,
            )
        )
        if found is None:
            raise QuotaExceededError()
        return found

    async def settle(
        self,
        user_id: uuid.UUID | str,
        *,
        source: str,
        settlement_key: str,
        amount: int,
        kind: str,
        model: str | None = None,
        conversation_id: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        image_count: int | None = None,
        extra: dict[str, Any] | None = None,
        quota_key: str = DEFAULT_QUOTA_KEY,
        trace_id: str | None = None,
    ) -> Settlement:
        """按结算键记一笔。重复键不再相加。缺用量记为未结算。"""
        existing = await self.session.scalar(
            select(UsageRecord).where(
                UsageRecord.user_id == user_id,
                UsageRecord.source == source,
                UsageRecord.settlement_key == settlement_key,
            )
        )
        if existing is not None:
            quota = await self.get_quota(user_id, quota_key)
            return Settlement(
                status="duplicate",
                amount=existing.amount,
                used_value=quota.used_value if quota is not None else 0,
                limit_value=quota.limit_value if quota is not None else 0,
                unlimited=quota is not None and quota.limit_source == "unlimited",
            )

        quota = await self.get_quota(user_id, quota_key)
        if quota is None and quota_key == DEFAULT_QUOTA_KEY:
            quota = await self.ensure_default_quota(user_id)
        if quota is not None:
            quota = await self.session.scalar(
                select(UsageQuota).where(UsageQuota.id == quota.id).with_for_update()
            )
        existing = await self.session.scalar(
            select(UsageRecord).where(
                UsageRecord.user_id == user_id,
                UsageRecord.source == source,
                UsageRecord.settlement_key == settlement_key,
            )
        )
        if existing is not None:
            return Settlement(
                status="duplicate",
                amount=existing.amount,
                used_value=quota.used_value if quota is not None else 0,
                limit_value=quota.limit_value if quota is not None else 0,
                unlimited=quota is not None and quota.limit_source == "unlimited",
            )
        billable = amount if amount > 0 else 0
        used_before = quota.used_value if quota is not None else 0
        used_after = used_before + billable
        limit_value = quota.limit_value if quota is not None else 0
        limit_source = quota.limit_source if quota is not None else "manual"
        status = settlement_result(limit_source, limit_value, used_after, billable)
        context: dict[str, Any] = {
            "kind": kind,
            "model": model,
            "conversation_id": conversation_id,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }
        if image_count is not None:
            context["image_count"] = image_count
        if extra:
            context.update(extra)
        record = UsageRecord(
            user_id=user_id,
            trace_id=trace_id,
            metric=kind,
            amount=billable,
            source=source,
            settlement_key=settlement_key,
            result="unsettled" if billable <= 0 else status,
            context_json=context,
        )
        try:
            async with self.session.begin_nested():
                if quota is not None and billable > 0:
                    quota.used_value = used_after
                self.session.add(record)
                await self.session.flush()
        except IntegrityError:
            if quota is not None:
                await self.session.refresh(quota)
            raced = await self.session.scalar(
                select(UsageRecord).where(
                    UsageRecord.user_id == user_id,
                    UsageRecord.source == source,
                    UsageRecord.settlement_key == settlement_key,
                )
            )
            return Settlement(
                status="duplicate",
                amount=raced.amount if raced is not None else 0,
                used_value=quota.used_value if quota is not None else 0,
                limit_value=limit_value,
                unlimited=limit_source == "unlimited",
            )
        return Settlement(
            status=record.result,
            amount=billable,
            used_value=quota.used_value if quota is not None else 0,
            limit_value=limit_value,
            unlimited=limit_source == "unlimited",
        )

    async def list_usage_records(
        self,
        user_id: uuid.UUID | str,
        *,
        limit: int = 50,
        offset: int = 0,
        kind: str | None = None,
    ) -> tuple[list[UsageRecord], int, int, dict[str, int]]:
        """分页列出用户的额度消耗明细。

        返回 (records, total_count, total_consumed, kind_breakdown)。
        - total_consumed：该用户累计消耗 token 总数（所有 kind）。
        - kind_breakdown：按 kind 分组的消耗 token 汇总，供前端汇总卡展示。
        """
        from sqlalchemy import func

        owned = select(UsageRecord).where(UsageRecord.user_id == user_id)
        base = owned.where(UsageRecord.metric == kind) if kind else owned
        count_query = select(func.count(UsageRecord.id)).where(UsageRecord.user_id == user_id)
        if kind:
            count_query = count_query.where(UsageRecord.metric == kind)

        record_count = await self.session.scalar(count_query) or 0
        total_consumed = await self.session.scalar(
            select(func.coalesce(func.sum(UsageRecord.amount), 0)).where(
                UsageRecord.user_id == user_id
            )
        ) or 0

        breakdown_rows = await self.session.execute(
            select(UsageRecord.metric, func.coalesce(func.sum(UsageRecord.amount), 0))
            .where(UsageRecord.user_id == user_id)
            .group_by(UsageRecord.metric)
        )
        kind_breakdown: dict[str, int] = {}
        for metric, summed in breakdown_rows.all():
            key = metric if metric in ("chat", "image") else "chat"
            kind_breakdown[key] = kind_breakdown.get(key, 0) + int(summed)

        rows = list(
            await self.session.scalars(
                base.order_by(UsageRecord.created_at.desc()).limit(limit).offset(offset)
            )
        )
        return rows, int(record_count), int(total_consumed), kind_breakdown
