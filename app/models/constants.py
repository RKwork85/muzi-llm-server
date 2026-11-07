"""持久化状态取值。任务状态与内存中的 TaskStatus 对齐，但不驱动运行时状态机。"""

from __future__ import annotations

from app.agents.models import TaskStatus, ToolExecutionStatus

USER_STATUSES = ("active", "disabled")
CREDENTIAL_PROVIDERS = ("email_password", "github")
SESSION_MODES = ("chat", "agent")
SESSION_STATUSES = ("active", "archived", "closed")
MESSAGE_ROLES = ("user", "assistant", "system", "tool")
TASK_TYPES = ("chat", "agent")
TASK_STATUSES = tuple(status.value for status in TaskStatus)
EXECUTION_STATUSES = ("pending", "running", "succeeded", "failed", "cancelled")
TOOL_CALL_STATUSES = ("running", *(status.value for status in ToolExecutionStatus))


def sql_in(column: str, values: tuple[str, ...]) -> str:
    if not values or any("'" in item for item in values):
        raise ValueError("非法枚举值")
    joined = ", ".join(f"'{item}'" for item in values)
    return f"{column} IN ({joined})"
