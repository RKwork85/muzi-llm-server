# A10 Agent Runtime 集成审计

## 实际接入路径

```text
stdio:
  lifespan -> McpSession.start -> register_mcp_tools(agent_registry)
  -> chat_mode_tool_names / agent toolset
  -> ToolService.execute

远程:
  POST /api/v1/chat/completions/stream
  -> 仅 agent 模式 -> McpService.tool_specs(user_id)
  -> prepare_stream 把 schema 交给模型
  -> overlay_tools 复制当前 registry 并注册这些 spec
  -> ToolService.execute

AgentRuntime（app/agents/runtime.py、loop.py、executor.py）:
  使用启动时的 agent_registry
  不调用 McpService.tool_specs
```

`grep` 在 `app/agents` 中没有 `McpService`、`overlay_tools` 或 `tool_specs`。

| ID | 项 | 等级 | 结果 | 证据 |
|---|---|---|---|---|
| RT-01 | 内部工具名含 server | PROJECT | PASS | `mcp__{server}__{tool}`。stdio server 来自 `mcp_server_name`，远程来自目录名 |
| RT-02 | schema 保留 | SHOULD | PARTIAL | 传给模型的是裁剪后的 schema，不是远端原文。见 A05-04 |
| RT-03 | 结果和错误转换 | PROJECT | PASS | 成功结果是 JSON 字符串，外层 `ToolService` 再包 `source/name/result`。失败变成 `ToolExecutionError`，不是成功 tool message |
| RT-04 | 模型能否看见工具 | PROJECT | PASS | `test_ask_stream_calls_allowlisted_tool`：agent 模式提供 `mcp__fake__echo`，不提供 `secret`。远程测试同样看到 `mcp__demo__echo` |
| RT-05 | 权限后执行 | PROJECT | PARTIAL | `ToolService.execute` 有 `allowed` 集合。远程集合等于本次 `tool_specs`。stdio 集合来自模式解析，不看用户。AgentRuntime 的 `ExecutionPolicy` 不包裹远程调用 |
| RT-06 | 轮数上限 | PROJECT | PASS | `prepare_stream` 使用 `max_tool_rounds`，循环超过上限发出 `ToolRoundLimitError`。这是既有对话循环，不是 MCP 单独实现 |
| RT-07 | 上下文占用 | SHOULD | PARTIAL | 描述截到 240，schema 被削薄，结果有 `tool_result_max_chars` 截断并保持 JSON。没有按 MCP 工具数量做目录预算 |
| RT-08 | 敏感内容 | PROJECT | PARTIAL | 有 `UNTRUSTED_PROMPT`。远程文本不做 `_plain` 清洗。令牌加密不依赖这段提示 |
| RT-09 | 长任务、取消、恢复 | SHOULD | FAIL | 对话工具超时只取消本地 `wait_for`。不写任务表，不发送 MCP cancellation。stdio 进程在超时后仍在。远程工具没有进入 `TaskManager` |
| RT-10 | Ask 不自动执行外部副作用 | PROJECT | PASS | 非 agent 模式丢掉 remote。ask 工具名列表排除 MCP。测试覆盖 ask 与 plan |
| RT-11 | Agent 模式继续受策略约束 | PROJECT | PARTIAL | agent 对话受 toolset、风险等级和 `ToolService` 约束。风险等级被 MCP 注册硬编码为 `low`，所以“低风险”过滤对 MCP 没有区分度 |
| RT-12 | 目录缓存与更新 | SHOULD | NOT_IMPLEMENTED | 每次 `tool_specs` 都重新 `tools/list`。没有 list_changed，也没有失效协议 |
| RT-13 | 两条运行时不一致 | PROJECT | FAIL | 流式 Chat 有按用户的远程工具。`AgentRuntime` 只有启动时的 stdio 工具。同一个“Agent”对外不是同一套 MCP |

## 结论

MCP 工具进入了现有 Registry 和 `ToolService`，没有另造一套执行器。这符合项目要求的方向。接入只覆盖流式 Chat 的 agent 模式；任务型 AgentRuntime 没有用户级远程 MCP。策略集成是部分完成。
