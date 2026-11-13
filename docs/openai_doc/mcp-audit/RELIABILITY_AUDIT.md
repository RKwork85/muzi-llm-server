# A11 可靠性审计

| ID | 项 | 等级 | 结果 | 证据 |
|---|---|---|---|---|
| REL-01 | 请求超时 | MUST | PASS | `McpSession._request` 使用 `asyncio.wait_for(..., mcp_timeout_seconds)`。`ToolService._run` 再包一层工具超时。`test_unknown_process_and_timeout_do_not_restart` 断言 hang 模式变成 `ToolExecutionError` |
| REL-02 | 连接和启动超时 | SHOULD | PARTIAL | stdio 启动失败会关闭会话。没有单独的进程启动超时；子进程启动挂起时不受 `mcp_timeout_seconds` 约束 |
| REL-03 | 超时后的服务端工作 | SHOULD | FAIL | 超时不发送 `notifications/cancelled`，不关闭 HTTP 请求的应用层取消，不杀掉 stdio 进程。测试断言超时后的 process 对象仍是原来的 |
| REL-04 | 重试 | MUST | PASS | 工具调用没有无条件重试。刷新令牌只在过期时做一次，失败则 `needs_reauth`。这避免了对副作用工具的盲目重试 |
| REL-05 | 退避和幂等分类 | SHOULD | FAIL | 没有传输层退避。stdio 和未命中写入正则的远程工具被标成幂等，分类依据不是协议 annotation |
| REL-06 | 取消传播 | SHOULD | FAIL | `ToolService` 在超时或 gather 失败时取消 Python 任务。MCP 会话没有对应的协议取消或子进程终止 |
| REL-07 | 错误映射 | SHOULD | FAIL | JSON-RPC error、HTTP 传输错误、工具 `isError` 都变成 `RuntimeError`，再被 `ToolService` 收成 `ToolExecutionError`。调用方看不到协议错误码。好处是这些失败不会被当成正常工具结果 |
| REL-08 | 认证失败 | PROJECT | PASS | HTTP 401 -> `PermissionError` -> `LoginRequiredError`，状态 `needs_reauth` |
| REL-09 | 并发 | SHOULD | FAIL | `ToolService.execute` 用 `asyncio.gather` 并行最多 5 个工具。stdio 有写锁和按 id 匹配，多请求在单线程事件循环上可区分。Legacy SSE 共用一个 `sse_lines` 迭代器，并行 `tools/call` 会抢同一条流。`RemoteHub` 也没有按会话加锁。测试都是单次调用 |
| REL-10 | 单 Server 拖垮进程 | SHOULD | PARTIAL | 远程列表失败被 Chat 端点吞掉并记日志。stdio 读循环异常只把当前会话标死，不崩溃主进程。没有熔断 |
| REL-11 | 资源上限 | PROJECT | PARTIAL | 消息 1 MB，工具结果按全局字符数截断，每工具每轮最多 5 次调用由 spec 声明。HTTP 连接池无 MCP 专用上限 |

可靠性的可用部分是超时、失败不重试、认证失败会要求重新授权。取消、错误分类和 SSE 并发没有达到可上线的水平。
