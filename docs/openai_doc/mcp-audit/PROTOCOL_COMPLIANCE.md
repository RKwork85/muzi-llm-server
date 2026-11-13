# A01 协议符合性

主规范：JSON-RPC 2.0；MCP `2024-11-05` lifecycle；对照官方 `2026-07-28`（2026-11-08 读取）。本项目是 Client，下列“服务端必须”项只在项目自己发出或处理消息时检查。

| ID | 项 | 等级 | 版本 | 结果 | 证据 |
|---|---|---|---|---|---|
| A01-01 | JSON-RPC 请求含 `jsonrpc`、`id`、`method`、`params` | MUST | 通用 | PASS | `McpSession._request`、`RemoteHub._rpc`。假服务按该格式应答，13 项测试通过 |
| A01-02 | 成功响应按 id 关联 | MUST | 通用 | PASS | stdio 用 `_pending[id]`；HTTP 检查 `message.get("id") != message_id` 后抛错 |
| A01-03 | 通知无 id | MUST | 通用 | PASS | `_notify` 发送 `notifications/initialized`。假 HTTP 服务对它返回 202 |
| A01-04 | 错误对象和标准错误码处理 | MUST | 通用 | PARTIAL | 对外拒绝反向调用时发送 `-32601`。入站 `error` 一律变成 `RuntimeError("mcp request failed")`，不区分 `-32600/-32602/-32700` |
| A01-05 | 非法 JSON | MUST | 通用 | PARTIAL | stdio `json.loads` 失败会结束读循环。`test_unknown_process_and_timeout_do_not_restart` 覆盖坏启动。没有独立的 `-32700` 响应测试 |
| A01-06 | `initialize` + `notifications/initialized` | MUST | 2024-11-05 | PASS | 两个客户端都发送。stdio 与 HTTP 假服务测试通过 |
| A01-07 | `protocolVersion`、`clientInfo`、capabilities | MUST | 2024-11-05 | PARTIAL | 请求体有这三项，capabilities 恒为 `{}`。不校验响应中的 `protocolVersion`、`serverInfo`、`capabilities` |
| A01-08 | 不支持的协议版本必须终止 | MUST | 2024-11-05 及 legacy | FAIL | stdio 忽略返回版本。HTTP 接受任意字符串并写入后续请求头 |
| A01-09 | 未知方法 | SHOULD | 通用 | PARTIAL | 只对 resources、prompts、sampling、elicitation、roots 回 `-32601`。其他带 method 和 id 的服务端请求被忽略，不回错误 |
| A01-10 | 2026-07-28 无状态请求 | MUST | 2026-07-28 | FAIL | 无 `_meta["io.modelcontextprotocol/protocolVersion"]`，无 `clientCapabilities`，无 `server/discover` |
| A01-11 | 不再依赖 `Mcp-Session-Id` | MUST | 2026-07-28 | FAIL | `RemoteHub._headers` 在有 session id 时发送 `Mcp-Session-Id`。这符合更早的 Streamable HTTP，不符合 2026-07-28 |
| A01-12 | `Mcp-Method` / `Mcp-Name` 与 body 一致 | MUST | 2026-07-28 HTTP | NOT_IMPLEMENTED | 请求头未发送这两项 |
| A01-13 | 能力与行为一致 | MUST | 通用 | PASS | 客户端没有声明 resources、prompts、sampling。这些方法被拒绝，测试 `test_handshake_allowlist_env_and_reverse` 覆盖反向请求 |

## 结论

这是一个真实的 legacy MCP tools 客户端，不是普通 REST 工具包装。它能完成 `2024-11-05` 的 initialize 和 tools 往返，测试用假 Server 证明了这一点。

它没有完成 legacy 规范里的版本协商 MUST。它也没有实现 `2026-07-28` 的无状态协议。A01 不能标成整体 PASS。
