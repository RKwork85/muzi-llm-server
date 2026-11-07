# M3 验收

验收日期：2026-11-06。结论：离线测试和真实 DeepSeek 联调都已通过。

## 功能

- ContextManager 保留系统提示词和当前用户消息，按完整工具组裁剪，超预算时失败。
- AgentLoop 用 FakeProvider 完成纯文本、单工具、多工具、工具失败、未授权工具、重复调用 ID、步数上限、工具次数上限、Deadline 和非正常 `finish_reason`。
- AgentRuntime 负责策略、时钟、循环、线程池和终态。终态只写一次。
- TaskManager 支持创建、查询、取消、活动任务上限和 TTL 清理。数据只在本进程内存中。
- EventPublisher 有界，丢弃多余进度事件并计数，载荷不含思维链和工具参数。
- API：`POST /api/v1/agents/runs`、`GET /api/v1/agents/runs/{task_id}`、`POST /api/v1/agents/runs/{task_id}/cancel`、`GET /api/v1/agents/runs/{task_id}/events`。
- 端到端 mock：用户请求、calculator 工具调用、工具结果、最终答复。另有超时、取消、SSE 断连和第一阶段对话回归。

## 安全与资源

- 请求不能提交工具实现、handler 或任意 schema。额外字段返回 422。
- `allowed_tools` 超出服务端名单返回 403，Provider 不会被调用。
- 更大的 `max_steps` 返回 422，不会静默放宽。
- 工具仍经 ToolRegistry 和 ToolExecutor 执行。
- 参数长度和嵌套深度有上限。无法形成合法 JSON 的成功截断改为失败。
- 同步工具线程有硬上限。迟到结果不能变成成功。
- 取消传播 `CancelledError`。SSE 断开不取消任务。

## 兼容

- 第一阶段路由、Schema、错误码和 SSE 事件名没有替换。
- 全量测试没有删除或跳过原有用例。
- 新增配置都有默认值和上下限：`AGENT_MAX_ACTIVE_TASKS`、`AGENT_TASK_TTL_SECONDS`、`AGENT_SYNC_TOOL_CONCURRENCY`、`AGENT_CANCEL_CLEANUP_SECONDS`。

## 测试

实施阶段的离线结果是 `150 passed, 4 skipped in 4.31s`。复核时补了 402 专项和两条 Agent Live 测试。

关闭 Live 开关：

```text
.\.venv\Scripts\python.exe -m pytest -q --tb=short
152 passed, 6 skipped in 6.14s
```

打开 `DEEPSEEK_LIVE=1`，模型 `deepseek-flash`：

```text
.\.venv\Scripts\python.exe -m pytest -q --tb=short
158 passed in 12.99s
```

真实 API 覆盖 6 条，全部通过：

- `test_live_chat_completion_and_schema`
- `test_live_sse_streams_until_done`
- `test_live_tool_loop_executes_and_answers`
- `test_live_short_timeout_is_safe`
- `test_live_agent_tool_round_trip`
- `test_live_agent_deadline_stops_the_run`

`git diff --check` 通过。没有失败用例。

## 已知限制

- 内存任务不能跨 worker 或进程重启查询。
- 同步工具线程超时后仍会运行到函数返回。
- 第一阶段 ToolService 的旧截断回退没有改。
- 第一阶段对 HTTP 402 的公开错误码仍是 `UPSTREAM_ERROR`。Agent 通过内部 `upstream_status` 识别余额不足。
