"""让既有 Agent 接口测试通过新的登录门槛，不放宽生产入口。"""

from __future__ import annotations

from types import SimpleNamespace

from app.authorization.domain.catalog import ROLE_BY_CODE
from app.core.exceptions import LoginRequiredError
from app.authorization.application.execution_guard import ExecutionContext

AGENT_TOKEN = "legacy-agent-access-token"
AGENT_OWNER = "legacy-agent-owner"
AGENT_HEADERS = {"Authorization": f"Bearer {AGENT_TOKEN}"}
# 既有功能测试需要读取工作区和调用已授权 MCP。不包含写入、删除和命令权限。
LEGACY_TOOL_PERMISSIONS = frozenset(ROLE_BY_CODE["member"].permissions) | frozenset(
    {"ask.workspace.read", "plan.workspace.read", "agent.workspace.read", "mcp.execute"}
)


def allow_legacy_agent_api(monkeypatch) -> None:
    """任何非空令牌都映射到同一个测试用户。安全测试不要调用它。"""

    async def require_agent_operator(credentials):
        if credentials is None or not credentials.credentials:
            raise LoginRequiredError()
        return SimpleNamespace(id=AGENT_OWNER)

    async def authenticate_request_user(credentials):
        if credentials is None or not credentials.credentials:
            raise LoginRequiredError()
        return AGENT_OWNER

    monkeypatch.setattr("app.api.v1.endpoints.agents.require_agent_operator", require_agent_operator)
    monkeypatch.setattr("app.api.v1.endpoints.agents.authenticate_request_user", authenticate_request_user)
    monkeypatch.setattr("app.api.v1.endpoints.chat.require_agent_operator", require_agent_operator)

    async def load_execution_context(user_id, **kwargs):
        return ExecutionContext(
            user_id=str(user_id),
            runtime_mode=kwargs.get("runtime_mode") or "agent",
            run_id=kwargs.get("run_id"),
            conversation_id=kwargs.get("conversation_id"),
            workspace_scope=kwargs.get("workspace_scope"),
            effective_permissions=LEGACY_TOOL_PERMISSIONS,
            owned_workspace_scopes=frozenset(),
            authorization_source="test-legacy",
        )

    monkeypatch.setattr("app.api.v1.endpoints.agents.load_execution_context", load_execution_context)
    monkeypatch.setattr("app.api.v1.endpoints.chat.load_execution_context", load_execution_context)
    monkeypatch.setattr("app.api.v1.endpoints.planning.load_execution_context", load_execution_context)
