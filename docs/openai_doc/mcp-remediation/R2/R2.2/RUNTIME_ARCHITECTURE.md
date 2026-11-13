# R2.2 统一 MCP Runtime

Chat 和 AgentRuntime 共用 `app/mcp/execution.py` 里的 `run_mcp`。AgentRuntime、AgentLoop、ToolExecutor 没有重写。远程工具只在单次运行的注册表里出现，不写进全局 Agent 注册表。

## 组件

- 执行上下文：`MCPExecutionContext`。用户、会话和运行编号来自服务端 `McpScope`。
- 解析：`context_for` 只解析工具名 `mcp__{connector}__{tool}`。模型参数里的 `user_id`、`connector_id`、`connector` 会在调用前丢掉。
- 授权：stdio 走 `stdio_permitted`。远程工具在真正调用前再读一次该用户的 `selected_tools`。
- 分发：stdio 仍调用原来的会话，远程仍调用原来的 `_rpc`。
- 结果：成功结果最长 4000 字，超出部分截断。异常继续向外抛出，不把进程打崩。

## 两条入口

- Chat 的 agent 模式在流开始前设置 `CURRENT_MCP_USER` 和 `CURRENT_MCP_SCOPE`。
- `AgentRuntime.start` 接收可选的 `mcp_service`。已登录且工具集为默认或包含 `mcp` 时，把该用户的远程工具叠进这次运行，并把这些名字加入允许列表。
- Ask 和 Plan 不设置 MCP 用户，也不提供 MCP 工具。

没有 `McpScope` 的直接调用仍走原来的用户检查，避免把测试里的显式调用误判成匿名拒绝。范围一旦存在，就必须和工具绑定的用户一致。
