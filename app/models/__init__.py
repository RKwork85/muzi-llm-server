"""导入全部模型，供 Alembic 和脱敏监听注册。"""

from app.core.database.redaction import register_redaction_listeners
from app.models.base import Base
from app.models.planning import (
    PersistentInteraction,
    PersistentInteractionSubmission,
    PersistentPlan,
    PersistentPlanDecision,
    PersistentPlanRevision,
    PersistentPlanningEvent,
    PersistentPlanningOutbox,
)
from app.models.tables import (
    AgentTask,
    ConversationMessage,
    ConversationSession,
    ExecutionEvent,
    RefreshToken,
    TaskExecution,
    ToolCallRecord,
    User,
    UserCredential,
)

register_redaction_listeners()

__all__ = [
    "AgentTask",
    "Base",
    "PersistentInteraction",
    "PersistentInteractionSubmission",
    "PersistentPlan",
    "PersistentPlanDecision",
    "PersistentPlanRevision",
    "PersistentPlanningEvent",
    "PersistentPlanningOutbox",
    "ConversationMessage",
    "ConversationSession",
    "ExecutionEvent",
    "RefreshToken",
    "TaskExecution",
    "ToolCallRecord",
    "User",
    "UserCredential",
]
