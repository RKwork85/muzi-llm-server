# R2.2 测试

`tests/test_mcp_r2_platform.py` 覆盖：

- 上下文只来自服务端范围。
- 连接器与工具名不一致时不发出调用。
- 非幂等失败不重试。
- AgentRuntime 只把远程工具交给对应的已登录用户，另一个用户的请求看不到该工具。

同轮回归包含 R1 安全、R2.1 协议、远程 MCP、stdio、Chat、AgentRuntime 和工具框架。预先存在的 `test_deadline_cancel_and_disconnect` 未纳入，它在本轮改动前就会因模型超时失败。

判定：R2.2 通过。这不表示整个 MCP 已经可以按 2026-07-28 上线。
