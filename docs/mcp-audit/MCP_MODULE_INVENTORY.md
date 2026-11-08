# MCP 模块清单

状态含义见审计规范：`VERIFIED` 表示有实现且有测试或实测；`IMPLEMENTED_UNVERIFIED` 表示有代码但缺少对应验证；`NOT_IMPLEMENTED` 表示没有该能力；`N/A` 表示本项目不承担该角色。

| 模块 | 位置 | 状态 | 说明 |
|---|---|---|---|
| 官方 MCP SDK | `requirements.txt`；运行时 `find_spec("mcp")` | NOT_IMPLEMENTED | 未依赖官方 SDK |
| stdio Client | `app/tools/mcp_client.py` | VERIFIED | 握手、`tools/list`、`tools/call`。测试 `tests/agents/test_mcp.py` |
| 假 stdio Server | `tests/agents/mcp_fake_server.py` | VERIFIED | 只存在于测试，不是产品服务 |
| Streamable HTTP Client | `app/mcp/http_client.py` `RemoteHub._http` | VERIFIED | 假服务覆盖 initialize、tools/list、tools/call。`tests/test_remote_mcp.py` |
| Legacy SSE Client | `app/mcp/http_client.py` `RemoteHub._sse` | VERIFIED | `test_sse_transport_calls_the_selected_tool` 在本机 uvicorn 上走通 |
| 远程会话池 | `app/mcp/http_client.py` `_HUBS`、`RemoteHub._sessions` | IMPLEMENTED_UNVERIFIED | 按 `(user_id, connector)` 缓存。无并发和故障切换测试 |
| OAuth 客户端 | `app/mcp/service.py` | VERIFIED | PKCE、state、刷新、动态注册。假授权服务器测试通过 |
| 令牌密封 | `app/mcp/crypto.py` | VERIFIED | 往返和“密文不含明文”有测试。算法不是标准 AEAD |
| 连接存储 | `app/mcp/store.py`，`app/models/tables.py` | PARTIAL | 内存存储被测试覆盖。SQL 路径和迁移 `b7e1c0a94d11` 未在本轮执行 |
| 连接器目录 | `app/mcp/catalog.py`，`app/mcp/connectors.toml` | VERIFIED | 非 HTTPS 在 `allow_insecure=false` 时被拒绝 |
| 管理 API | `app/api/v1/endpoints/mcp.py` | VERIFIED | 未登录连接返回 401；回调失败不回显 token |
| 工具注册 | `register_mcp_tools`、`McpService._spec`、`overlay_tools` | VERIFIED | 名称前缀 `mcp__{server}__{tool}`，结果包 `untrusted` |
| ToolService 执行 | `app/services/tool_service.py` | VERIFIED | 白名单、缺参、超时失败有 stdio 测试 |
| 对话接入 | `chat.py`、`chat_service.py`、`toolsets.py` | VERIFIED | agent 模式可见已选工具；ask/plan 不可见 stdio MCP |
| AgentRuntime 远程工具 | `app/agents/` | NOT_IMPLEMENTED | `app/agents` 中没有 `McpService` 或 `overlay_tools` |
| MCP Protocol Server | 全仓库 | N/A | 没有对外 MCP Server。不因此判定 Client 项目不合格 |
| Resources 客户端 | 两处 `_REJECTED` | NOT_IMPLEMENTED | 主动拒绝 `resources/*`。客户端能力声明为 `{}` |
| Prompts 客户端 | 同上 | NOT_IMPLEMENTED | 主动拒绝 `prompts/*` |
| Sampling / Elicitation / Roots | 同上 | NOT_IMPLEMENTED | 收到服务端反向请求时返回 `-32601` |
| `server/discover` | 全仓库无该方法字符串 | NOT_IMPLEMENTED | 2026-07-28 能力 |
| 取消、进度、分页 | 客户端代码 | NOT_IMPLEMENTED | 无 `notifications/cancelled`、`notifications/progress`、`nextCursor` |
| 指标与链路 | 日志调用点 | NOT_IMPLEMENTED | 只有少量 info/error 日志 |

产品角色可以确定为：**MCP Host 里的自定义 Client**。它连接管理员配置的本地进程和目录中的远程 Server，自己不对外扮演 MCP Server。
