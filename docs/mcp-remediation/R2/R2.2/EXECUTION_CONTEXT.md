# 执行上下文

`McpScope` 只由服务端写入：

| 字段 | 来源 |
| --- | --- |
| user_id | 请求认证结果 |
| request_id | 当前请求编号 |
| conversation_id | Chat 能提供时写入，否则为空 |
| agent_run_id | Agent 任务编号，Chat 为空 |

`MCPExecutionContext` 在此基础上增加 `connector_id`、`tool_name`、`local_name`、`timeout`、`permission_scope` 和 `idempotent`。`permission_scope` 固定为 `execute`。超时使用 `mcp_timeout_seconds`。

工具参数不能替换这些字段。名字无法解析成三段，或者范围里的用户和工具绑定用户不一致时，调用被拒绝。
