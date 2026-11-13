# 运行时回归

AgentRuntime 用一次运行内的注册表承接远程工具。Ask、Plan 和非 MCP 工具的边界没有改。Chat 不再把 stdio 工具重复铺到远程列表上。

通过的相关文件包括：

- `tests/test_mcp_r2_platform.py`
- `tests/agents/test_mcp.py`
- `tests/agents/test_runtime.py`
- `tests/agents/test_tool_framework.py`
- `tests/agents/test_executor.py`
- `tests/test_chat.py`
- `tests/api/test_agent_api.py`，排除预先失败的 `test_deadline_cancel_and_disconnect`

远程工具在对应用户的 Agent 运行中被实际调用。另一个用户的请求不包含该工具名。
