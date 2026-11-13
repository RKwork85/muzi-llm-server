"""用户数据清理服务：删除用户、删除对话数据、删除额度信息。

删除顺序严格遵循外键约束：

- RESTRICT 外键必须先于被引用行删除：
  - ``agent_tasks.user_id`` / ``agent_tasks.session_id``
  - ``sessions.user_id``
  - ``ask_runs.owner_id``
- 其余多为 ON DELETE CASCADE，随父表删除自动清理（messages、executions、
  tool_calls、events、plans、plan_events、chat_history_messages 等）。
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ask_run import AskRun
from app.models.metering import (
    LlmUsageRecord,
    QuotaTransaction,
    SkillUsageRecord,
    SummaryUpdate,
    ToolUsageRecord,
)
from app.models.rbac import QuotaAdjustment, UsageQuota, UsageRecord
from app.models.tables import (
    AgentTask,
    ChatHistorySession,
    ConversationSession,
    User,
)


class UserCleanupService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def delete_user(self, user_id: uuid.UUID | str) -> dict[str, int]:
        """删除用户及其全部关联数据。"""
        stats = await self._delete_conversations(user_id)
        result = await self.session.execute(delete(User).where(User.id == user_id))
        stats["users"] = result.rowcount
        return stats

    async def delete_conversations(self, user_id: uuid.UUID | str) -> dict[str, int]:
        """删除用户的对话相关数据，保留账号、额度与凭证。"""
        return await self._delete_conversations(user_id)

    async def delete_quota(self, user_id: uuid.UUID | str) -> dict[str, int]:
        """删除用户的额度相关数据，保留账号与对话。"""
        stats: dict[str, int] = {}
        # 先删引用 llm_usage_records 的明细（外键为 SET NULL / CASCADE，显式删除更清晰）。
        for model, key in (
            (SummaryUpdate, "summary_updates"),
            (SkillUsageRecord, "skill_usage_records"),
            (ToolUsageRecord, "tool_usage_records"),
            (QuotaTransaction, "quota_transactions"),
            (LlmUsageRecord, "llm_usage_records"),
            (UsageRecord, "usage_records"),
            (QuotaAdjustment, "quota_adjustments"),
            (UsageQuota, "usage_quotas"),
        ):
            result = await self.session.execute(delete(model).where(model.user_id == user_id))
            stats[key] = result.rowcount
        return stats

    async def _delete_conversations(self, user_id: uuid.UUID | str) -> dict[str, int]:
        """按外键依赖顺序删除对话相关数据。

        1. ask_runs（owner_id RESTRICT；conversation_id CASCADE 指向 sessions）
        2. agent_tasks（user_id RESTRICT；session_id RESTRICT 指向 sessions）
           —— 级联 executions → tool_calls / events
        3. sessions（user_id RESTRICT）
           —— 级联 messages / plans / plan_events / ask_runs(conversation)
        4. chat_history_sessions（user_id CASCADE）—— 级联 chat_history_messages
        """
        stats: dict[str, int] = {}
        result = await self.session.execute(delete(AskRun).where(AskRun.owner_id == user_id))
        stats["ask_runs"] = result.rowcount
        result = await self.session.execute(delete(AgentTask).where(AgentTask.user_id == user_id))
        stats["agent_tasks"] = result.rowcount
        result = await self.session.execute(
            delete(ConversationSession).where(ConversationSession.user_id == user_id)
        )
        stats["sessions"] = result.rowcount
        result = await self.session.execute(
            delete(ChatHistorySession).where(ChatHistorySession.user_id == user_id)
        )
        stats["chat_history_sessions"] = result.rowcount
        return stats
