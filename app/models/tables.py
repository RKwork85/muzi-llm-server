"""九张业务表。只描述持久化结构，不实现 Agent 状态机或事件分发。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.constants import (
    CREDENTIAL_PROVIDERS,
    EXECUTION_STATUSES,
    MESSAGE_ROLES,
    SESSION_MODES,
    SESSION_STATUSES,
    TASK_STATUSES,
    TASK_TYPES,
    TOOL_CALL_STATUSES,
    USER_STATUSES,
    sql_in,
)
from app.models.mixins import UUIDPrimaryKeyMixin, CreatedAtMixin, TimestampMixin

_JSON_OBJECT = text("'{}'::jsonb")


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        UniqueConstraint("username", name="uq_users_username"),
        CheckConstraint(sql_in("status", USER_STATUSES), name="ck_users_status"),
        CheckConstraint(
            "email = lower(email) AND position('@' in email) > 1",
            name="ck_users_email_normalized",
        ),
        CheckConstraint(
            "username ~ '^[a-z][a-z0-9_]{2,31}$'",
            name="ck_users_username_normalized",
        ),
        Index("ix_users_status", "status"),
        {"comment": "用户基本信息"},
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    username: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default=text("'active'")
    )
    preferences: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=_JSON_OBJECT
    )

    credentials: Mapped[list[UserCredential]] = relationship(
        back_populates="user",
        passive_deletes=True,
    )
    sessions: Mapped[list[ConversationSession]] = relationship(
        back_populates="user",
        passive_deletes=True,
    )
    tasks: Mapped[list[AgentTask]] = relationship(
        back_populates="user",
        passive_deletes=True,
    )
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user",
        passive_deletes=True,
    )


class UserCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_credentials"
    __table_args__ = (
        UniqueConstraint("provider", "provider_uid", name="uq_user_credentials_provider_uid"),
        CheckConstraint(sql_in("provider", CREDENTIAL_PROVIDERS), name="ck_user_credentials_provider"),
        CheckConstraint(
            "(provider = 'email_password' AND password_hash IS NOT NULL "
            "AND starts_with(password_hash, '$argon2id$')) "
            "OR (provider <> 'email_password' AND password_hash IS NULL)",
            name="ck_user_credentials_secret",
        ),
        CheckConstraint(
            "provider <> 'email_password' OR provider_uid = lower(provider_uid)",
            name="ck_user_credentials_email_uid",
        ),
        Index("ix_user_credentials_user_id", "user_id"),
        {"comment": "邮箱密码与第三方身份凭证"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_user_credentials_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_uid: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255))

    user: Mapped[User] = relationship(back_populates="credentials")


class ConversationSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint(sql_in("mode", SESSION_MODES), name="ck_sessions_mode"),
        CheckConstraint(sql_in("status", SESSION_STATUSES), name="ck_sessions_status"),
        Index("ix_sessions_user_updated", "user_id", "updated_at"),
        Index("ix_sessions_status", "status"),
        {"comment": "用户对话会话"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_sessions_user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default=text("''"))
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default=text("'active'")
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=_JSON_OBJECT,
    )

    user: Mapped[User] = relationship(back_populates="sessions")
    messages: Mapped[list[ConversationMessage]] = relationship(
        back_populates="session",
        passive_deletes=True,
    )
    tasks: Mapped[list[AgentTask]] = relationship(
        back_populates="session",
        passive_deletes=True,
    )


class ConversationMessage(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(sql_in("role", MESSAGE_ROLES), name="ck_messages_role"),
        Index("ix_messages_session_created", "session_id", "created_at"),
        {"comment": "会话消息。追加写入，不设 updated_at。"},
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sessions.id", name="fk_messages_session_id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(
        String(64), nullable=False, default="text", server_default=text("'text'")
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=_JSON_OBJECT,
    )

    session: Mapped[ConversationSession] = relationship(back_populates="messages")


class AgentTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (
        CheckConstraint(sql_in("task_type", TASK_TYPES), name="ck_agent_tasks_type"),
        CheckConstraint(sql_in("status", TASK_STATUSES), name="ck_agent_tasks_status"),
        Index("ix_agent_tasks_user_created", "user_id", "created_at"),
        Index("ix_agent_tasks_session_id", "session_id"),
        Index("ix_agent_tasks_status", "status"),
        Index("ix_agent_tasks_request_id", "request_id"),
        {"comment": "Agent 任务持久化记录。运行时状态机仍在进程内。"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_agent_tasks_user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sessions.id", name="fk_agent_tasks_session_id", ondelete="RESTRICT"),
    )
    request_id: Mapped[str | None] = mapped_column(String(128))
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="created", server_default=text("'created'")
    )
    input_payload: Mapped[Any] = mapped_column(
        "input", JSONB, nullable=False, default=dict, server_default=_JSON_OBJECT    )
    output_payload: Mapped[Any | None] = mapped_column("output", JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))

    user: Mapped[User] = relationship(back_populates="tasks")
    session: Mapped[ConversationSession | None] = relationship(back_populates="tasks")
    executions: Mapped[list[TaskExecution]] = relationship(
        back_populates="task",
        passive_deletes=True,
    )
    events: Mapped[list[ExecutionEvent]] = relationship(
        back_populates="task",
        passive_deletes=True,
    )


class TaskExecution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "executions"
    __table_args__ = (
        CheckConstraint(sql_in("status", EXECUTION_STATUSES), name="ck_executions_status"),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="ck_executions_time",
        ),
        Index("ix_executions_task_created", "task_id", "created_at"),
        Index("ix_executions_status", "status"),
        {"comment": "一次任务下的执行尝试。耗时由 finished_at - started_at 计算。"},
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_tasks.id", name="fk_executions_task_id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default=text("'pending'")
    )
    execution_input: Mapped[Any] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=_JSON_OBJECT    )
    execution_output: Mapped[Any | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    task: Mapped[AgentTask] = relationship(back_populates="executions")
    tool_calls: Mapped[list[ToolCallRecord]] = relationship(
        back_populates="execution",
        passive_deletes=True,
    )
    events: Mapped[list[ExecutionEvent]] = relationship(
        back_populates="execution",
        passive_deletes=True,
    )


class ToolCallRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tool_calls"
    __table_args__ = (
        CheckConstraint(sql_in("status", TOOL_CALL_STATUSES), name="ck_tool_calls_status"),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="ck_tool_calls_time",
        ),
        Index("ix_tool_calls_execution_created", "execution_id", "created_at"),
        Index("ix_tool_calls_tool_name_created", "tool_name", "created_at"),
        {"comment": "工具调用审计。参数和结果在写入前脱敏。"},
    )

    execution_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("executions.id", name="fk_tool_calls_execution_id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_call_id: Mapped[str | None] = mapped_column(String(128))
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    arguments: Mapped[Any] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=_JSON_OBJECT    )
    result: Mapped[Any | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="running", server_default=text("'running'")
    )
    error: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    execution: Mapped[TaskExecution] = relationship(back_populates="tool_calls")


class ExecutionEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("task_id", "sequence", name="uq_events_task_sequence"),
        CheckConstraint("sequence >= 1", name="ck_events_sequence"),
        Index("ix_events_execution_sequence", "execution_id", "sequence"),
        Index("ix_events_task_created", "task_id", "created_at"),
        Index("ix_events_created", "created_at"),
        Index("ix_events_type_created", "event_type", "created_at"),
        {"comment": "执行过程事件。只保存事实，不替代进程内事件队列。"},
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_tasks.id", name="fk_events_task_id", ondelete="CASCADE"),
        nullable=False,
    )
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("executions.id", name="fk_events_execution_id", ondelete="CASCADE"),
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[Any] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=_JSON_OBJECT    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    task: Mapped[AgentTask] = relationship(back_populates="events")
    execution: Mapped[TaskExecution | None] = relationship(back_populates="events")


class RefreshToken(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="ck_refresh_tokens_hash"),
        Index("ix_refresh_tokens_expires", "expires_at"),
        Index(
            "ix_refresh_tokens_active_user",
            "user_id",
            "expires_at",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        {"comment": "登录刷新凭证。只保存哈希，一行对应一个设备。"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_refresh_tokens_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="refresh_tokens")


class McpOAuthClient(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "mcp_oauth_clients"
    __table_args__ = (
        UniqueConstraint("connector_name", name="uq_mcp_oauth_clients_name"),
        {"comment": "远程 MCP 动态注册得到的客户端。密钥只存密文。"},
    )

    connector_name: Mapped[str] = mapped_column(String(32), nullable=False)
    client_id: Mapped[str] = mapped_column(String(256), nullable=False)
    client_secret_encrypted: Mapped[str | None] = mapped_column(Text)


class McpConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "mcp_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "connector_name", name="uq_mcp_connections_user_connector"),
        CheckConstraint(
            "status IN ('connected', 'needs_reauth')",
            name="ck_mcp_connections_status",
        ),
        Index("ix_mcp_connections_user", "user_id"),
        {"comment": "用户与远程 MCP 的授权。令牌只存密文。"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_mcp_connections_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    connector_name: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="connected")
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    selected_tools: Mapped[Any] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )


class McpOAuthState(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "mcp_oauth_states"
    __table_args__ = (
        UniqueConstraint("state_hash", name="uq_mcp_oauth_states_hash"),
        CheckConstraint("state_hash ~ '^[0-9a-f]{64}$'", name="ck_mcp_oauth_states_hash"),
        Index("ix_mcp_oauth_states_expires", "expires_at"),
        {"comment": "一次性 OAuth state。验证码只存密文。"},
    )

    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_mcp_oauth_states_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    connector_name: Mapped[str] = mapped_column(String(32), nullable=False)
    code_verifier_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class McpConnectorGrant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "mcp_connector_grants"
    __table_args__ = (
        UniqueConstraint("user_id", "connector_id", name="uq_mcp_connector_grants_user"),
        Index("ix_mcp_connector_grants_user", "user_id"),
        {"comment": "用户对某个 MCP 连接器的授权。不等于工具授权。"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_mcp_connector_grants_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    connector_id: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class McpToolGrant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "mcp_tool_grants"
    __table_args__ = (
        UniqueConstraint("user_id", "connector_id", "tool_name", name="uq_mcp_tool_grants_user_tool"),
        Index("ix_mcp_tool_grants_user", "user_id", "connector_id"),
        {"comment": "用户对某个连接器上单个工具的授权。"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_mcp_tool_grants_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    connector_id: Mapped[str] = mapped_column(String(32), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class McpAuthorizationAudit(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "mcp_authorization_audits"
    __table_args__ = (
        Index("ix_mcp_authorization_audits_user", "user_id", "created_at"),
        {"comment": "授权变更记录。不保存工具参数或令牌。"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_mcp_authorization_audits_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    connector_id: Mapped[str] = mapped_column(String(32), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)


def _register_listeners() -> None:
    from app.core.database.redaction import register_redaction_listeners

    register_redaction_listeners()


_register_listeners()
