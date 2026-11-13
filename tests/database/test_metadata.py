"""模型层约束。不连接数据库。"""

from __future__ import annotations

from app.models import Base

EXPECTED_TABLES = {
    "users",
    "user_credentials",
    "ask_runs",
    "sessions",
    "messages",
    "agent_tasks",
    "executions",
    "tool_calls",
    "events",
    "refresh_tokens",
    "mcp_oauth_clients",
    "mcp_connections",
    "mcp_oauth_states",
    "mcp_connector_grants",
    "mcp_tool_grants",
    "mcp_authorization_audits",
    "plans",
    "plan_revisions",
    "plan_decisions",
    "plan_interactions",
    "interaction_submissions",
    "plan_events",
    "plan_event_outbox",
    "plan_actions",
    "roles",
    "permissions",
    "user_roles",
    "role_permissions",
    "user_permissions",
    "resource_grants",
    "resource_shares",
    "access_audit_logs",
    "usage_quotas",
    "usage_records",
    "quota_adjustments",
    "ark_files",
    "chat_history_sessions",
    "chat_history_messages",
    "profile_edits",
    "organizations",
    "organization_members",
    "projects",
    "project_members",
}

ON_DELETE = {
    ("user_credentials", "user_id"): "CASCADE",
    ("ask_runs", "owner_id"): "RESTRICT",
    ("ask_runs", "conversation_id"): "CASCADE",
    ("sessions", "user_id"): "RESTRICT",
    ("messages", "session_id"): "CASCADE",
    ("agent_tasks", "user_id"): "RESTRICT",
    ("agent_tasks", "session_id"): "RESTRICT",
    ("executions", "task_id"): "CASCADE",
    ("tool_calls", "execution_id"): "CASCADE",
    ("events", "task_id"): "CASCADE",
    ("events", "execution_id"): "CASCADE",
    ("refresh_tokens", "user_id"): "CASCADE",
    ("mcp_connections", "user_id"): "CASCADE",
    ("mcp_oauth_states", "user_id"): "CASCADE",
    ("mcp_connector_grants", "user_id"): "CASCADE",
    ("mcp_tool_grants", "user_id"): "CASCADE",
    ("mcp_authorization_audits", "user_id"): "CASCADE",
    ("plans", "conversation_id"): "CASCADE",
    ("plans", "owner_id"): "CASCADE",
    ("plan_revisions", "plan_id"): "CASCADE",
    ("plan_decisions", "plan_id"): "CASCADE",
    ("plan_interactions", "plan_id"): "CASCADE",
    ("interaction_submissions", "plan_id"): "CASCADE",
    ("interaction_submissions", "interaction_id"): "CASCADE",
    ("interaction_submissions", "owner_id"): "CASCADE",
    ("plan_events", "plan_id"): "CASCADE",
    ("plan_events", "conversation_id"): "CASCADE",
    ("plan_event_outbox", "event_id"): "CASCADE",
    ("plan_actions", "plan_id"): "CASCADE",
    ("plan_actions", "owner_id"): "CASCADE",
    ("user_roles", "user_id"): "CASCADE",
    ("user_roles", "role_id"): "CASCADE",
    ("role_permissions", "role_id"): "CASCADE",
    ("role_permissions", "permission_id"): "CASCADE",
    ("user_permissions", "user_id"): "CASCADE",
    ("user_permissions", "permission_id"): "CASCADE",
    ("resource_grants", "user_id"): "CASCADE",
    ("resource_shares", "owner_id"): "CASCADE",
    ("resource_shares", "grantee_id"): "CASCADE",
    ("access_audit_logs", "actor_id"): "SET NULL",
    ("access_audit_logs", "subject_id"): "SET NULL",
    ("usage_quotas", "user_id"): "CASCADE",
    ("usage_quotas", "adjusted_by"): "SET NULL",
    ("quota_adjustments", "user_id"): "CASCADE",
    ("quota_adjustments", "actor_id"): "SET NULL",
    ("usage_records", "user_id"): "CASCADE",
    ("ark_files", "user_id"): "CASCADE",
    ("chat_history_sessions", "user_id"): "CASCADE",
    ("chat_history_messages", "session_id"): "CASCADE",
    ("profile_edits", "user_id"): "CASCADE",
    ("organization_members", "organization_id"): "CASCADE",
    ("organization_members", "user_id"): "CASCADE",
    ("projects", "organization_id"): "CASCADE",
    ("project_members", "project_id"): "CASCADE",
    ("project_members", "user_id"): "CASCADE",
}


def test_business_tables_exist() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_foreign_keys_use_explicit_delete_actions() -> None:
    found: dict[tuple[str, str], str] = {}
    for name, table in Base.metadata.tables.items():
        for column in table.columns:
            for key in column.foreign_keys:
                found[(name, column.name)] = key.ondelete
    assert found == ON_DELETE


def test_unique_constraints_and_primary_keys() -> None:
    users = Base.metadata.tables["users"]
    assert [column.name for column in users.primary_key.columns] == ["id"]
    names = {item.name for item in users.constraints}
    assert {"uq_users_email", "uq_users_username", "ck_users_email_normalized"} <= names
    events = Base.metadata.tables["events"]
    assert any(item.name == "uq_events_task_sequence" for item in events.constraints)
    tokens = Base.metadata.tables["refresh_tokens"]
    assert any(item.name == "uq_refresh_tokens_token_hash" for item in tokens.constraints)
    credentials = Base.metadata.tables["user_credentials"]
    credential_names = {item.name for item in credentials.constraints}
    assert "uq_user_credentials_provider_uid" in credential_names
    assert "ck_user_credentials_secret" in credential_names


def test_query_indexes_exist() -> None:
    indexes = {
        index.name
        for table in Base.metadata.tables.values()
        for index in table.indexes
    }
    expected = {
        "ix_sessions_user_updated",
        "ix_messages_session_created",
        "ix_agent_tasks_user_created",
        "ix_executions_task_created",
        "ix_tool_calls_execution_created",
        "ix_events_execution_sequence",
        "ix_events_created",
        "ix_refresh_tokens_active_user",
    }
    assert expected <= indexes


def test_json_columns_use_jsonb_and_metadata_is_not_an_attribute() -> None:
    from app.models.tables import ConversationSession

    sessions = Base.metadata.tables["sessions"]
    messages = Base.metadata.tables["messages"]
    tool_calls = Base.metadata.tables["tool_calls"]
    assert sessions.c.metadata.type.__class__.__name__ == "JSONB"
    assert messages.c.metadata.type.__class__.__name__ == "JSONB"
    assert tool_calls.c.arguments.type.__class__.__name__ == "JSONB"
    assert tool_calls.c.result.type.__class__.__name__ == "JSONB"
    column = ConversationSession.__mapper__.columns["metadata_json"]
    assert column.name == "metadata"
