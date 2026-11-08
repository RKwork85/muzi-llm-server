# A02 / A03 架构审计

## A02 Client

| ID | 项 | 等级 | 结果 | 证据 |
|---|---|---|---|---|
| A02-01 | Client 创建与启动 | PROJECT | PASS | `app/main.py` 在 `mcp_enabled` 时创建 `McpSession`。失败只记 `mcp startup failed`，聊天仍可启动。`test_failed_startup_still_serves_chat` |
| A02-02 | 关闭与读任务取消 | PROJECT | PASS | `McpSession.aclose` 关 stdin、等待或 kill 进程、取消 reader、给 pending future 设异常。测试 `finally` 后 `mcp_is_available()` 为 false |
| A02-03 | 远程会话创建 | PROJECT | PASS | `RemoteHub.call` 在 URL、token、transport 变化时新建 `RemoteSession`，并先 initialize |
| A02-04 | 多 Server | SHOULD | PARTIAL | 远程目录可以有多项，会话键是 `(user_id, name)`。stdio 是进程内单例 `_session`，配置只有一个 command |
| A02-05 | Server 故障隔离 | SHOULD | PARTIAL | 远程工具拉取失败时 `chat.py` 捕获异常并跳过，对话继续。stdio 启动失败不影响聊天。单个远程会话没有健康检查，失败后仍留在 `_sessions` |
| A02-06 | 工具名隔离 | MUST | PASS | 本地名 `mcp__{server}__{tool}`。注册前检查长度和字符。重名则跳过或由 `ToolRegistry.register` 拒绝 |
| A02-07 | 配置持久化 | PROJECT | PARTIAL | 远程连接写入 `mcp_connections`。目录本身是部署文件，请求不能提交 URL。`test_request_cannot_carry_a_command` 拒绝请求携带 `mcp_command`。SQL 持久化未在本轮跑迁移 |
| A02-08 | 连接池上限、重连 | SHOULD | NOT_IMPLEMENTED | 无池大小、无断线重连。超时后 stdio 进程保持原对象，`test_unknown_process_and_timeout_do_not_restart` 断言不会换进程 |
| A02-09 | 生命周期异常恢复 | SHOULD | FAIL | 超时或读失败把 `alive` 设为 false 或 future 失败，但不重建会话。下一次调用继续使用已死会话 |

stdio 环境只保留 `PATH` 和 `SYSTEMROOT`。`test_handshake_allowlist_env_and_reverse` 证明父进程的 `DEEPSEEK_API_KEY` 不会进入工具结果。命令必须是绝对路径上的文件，不经过 shell。

## A03 Server

| ID | 项 | 结果 | 说明 |
|---|---|---|---|
| A03-01 | 产品内 MCP Server | N/A | 仓库没有 MCP Server 的 request handler。`/api/v1/mcp` 是用户连接管理 API，入站不是 JSON-RPC |
| A03-02 | 测试假 Server | N/A | `mcp_fake_server.py` 和 `tests/test_remote_mcp.py` 的 Starlette 应用只服务测试 |

A03 不参与扣分。缺一个对外 MCP Server 不影响“纯 Client”定位。

## 架构判断

两条客户端栈并存：

1. 进程级 stdio，启动时注册进 `agent_registry`，所有用户共享。
2. 用户级远程 HTTP，每次 agent 对话按当前用户 `tool_specs` 叠加到临时 Registry。

这个分裂是事实，不是文档推断。`app/agents` 不引用远程 `McpService`。远程工具只出现在 `app/api/v1/endpoints/chat.py` 的流式对话里。
