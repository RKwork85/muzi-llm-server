"""AI 执行权限守卫（P3）。

原则：不能因为用户有 Agent 使用权限，就自动获得 Agent 所能调用的全部 Tool 权限。
每次 Tool 执行都要重新经过执行策略检查。

本模块提供 tool -> 权限码 的映射与判定入口，供 Agent Runtime / Chat Service 在
实际执行工具前调用。与 app/agents/toolsets.py 的「暴露给模型的能力裁剪」和
app/agents/policy.py 的「数值上限裁剪」共同构成执行安全链路：

    用户发起请求 → agent.execute 校验 → 能力裁剪（toolsets） → 策略裁剪（policy）
    → 每次工具执行前执行本守卫 → 拒绝/放行 → 审计
"""

from __future__ import annotations

import uuid
from typing import Any

# 工具集 -> 执行所需的权限码。默认只读工具不额外要求（只要具备 agent.execute）。
TOOLSET_PERMISSION: dict[str, str] = {
    "skills": "skill.execute",
    "mcp": "mcp.execute",
    "code": "agent.execute",  # 沙箱代码执行仍要求 agent.execute 之上可再收紧
    "workspace": "agent.execute",
    "delivery": "agent.execute",
}

# 写入/删除/命令等危险工具必须额外具备显式授权，不能仅凭基础 agent.execute 放行。
DANGEROUS_TOOLS: dict[str, str] = {
    "write_text_file": "agent.execute",
    "write_docx": "agent.execute",
    "delete_file": "agent.delete",
    "run_command": "agent.execute",
    "run_python": "agent.execute",
}


class ExecutionGuard:
    """工具执行前的权限判定。不依赖会话状态，只依赖传入的 user_id 与工具元数据。"""

    def __init__(self, authorizer: Any) -> None:
        # authorizer 提供 `has_permission(user_id, permission) -> bool`
        self._authorizer = authorizer

    async def allowed(
        self,
        user_id: uuid.UUID | str | None,
        tool_name: str,
        toolset: str,
        *,
        dangerous: bool = False,
    ) -> bool:
        # 无用户上下文（如纯 MCP 服务路径）时，由调用方策略决定，这里放行。
        if user_id is None:
            return True
        required = TOOLSET_PERMISSION.get(toolset)
        if required is not None and not await self._authorizer.has_permission(user_id, required):
            return False
        if dangerous:
            extra = DANGEROUS_TOOLS.get(tool_name)
            if extra is not None and not await self._authorizer.has_permission(user_id, extra):
                return False
        return True
