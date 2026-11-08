# 审计

有两层记录。

授权变更进入 `MemoryGrantStore.audits`，数据库模式下进入 `mcp_authorization_audits`。内容是谁、哪个连接器、哪个工具、什么动作、什么时间。

执行记录进入进程内 `audits` 列表，最多保留约 200 条。一条成功或失败记录包含 `request_id`、`conversation_id`、`agent_run_id`、`connector_id`、`tool_name`、`user_id` 和 `status`。它不保存工具参数、令牌、Cookie 或密钥。

`tool_call_id` 仍由 Agent 事件流携带，不复制进 MCP 审计正文，避免把模型参数一起留下来。
