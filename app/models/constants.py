"""持久化状态取值。任务状态与内存中的 TaskStatus 对齐，但不驱动运行时状态机。"""

from __future__ import annotations

from app.agents.models import TaskStatus, ToolExecutionStatus

USER_STATUSES = ("active", "disabled")
ROLE_STATUSES = ("active", "disabled")
GRANT_TYPES = ("allow", "deny")
RESOURCE_TYPES = (
    "agent",
    "skill",
    "mcp_connector",
    "mcp_tool",
    "plugin",
    "knowledge",
    "file",
    "session",
    "task",
)
# phone 与迁移 ck_user_credentials_provider 保持一致。
CREDENTIAL_PROVIDERS = ("email_password", "github", "phone")
PROFILE_EDIT_FIELDS = ("avatar", "display_name")
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
