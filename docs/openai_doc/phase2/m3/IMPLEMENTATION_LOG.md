# M3 实施记录

记录日期：2026-11-06。M3 代码留在工作区，没有提交。

## 基线

- 分支：`main`
- M1/M2 代码恢复点：`995a2078018797145fdc0dba36ff147a90c20c4d`
- 含本记录之前的基线文档提交：`b3c76d7b84b35b92bef6008cb3844d0c76a735e3`
- 标签 `v0.1.0-phase1-baseline` 仍指向 `7063d99a5ff8ae71643d792d4543c872b5848ff7`

补充过两次提交，见 `docs/phase2/M3_BASELINE.md`。之后的 Agent Runtime 修改都没有提交。

## M3.0 — 截断和参数解析

修改 `app/agents/executor.py` 和 `tests/agents/test_executor.py`。

- 工具结果无法装进合法 JSON 信封时返回 `failed` / `TOOL_RESULT_TOO_LARGE`，不再把切碎的字符串标成成功。
- 能装下截断标记时，仍重新序列化合法 JSON，并带上 `...[truncated]`。
- 参数最长 8192 个字符，嵌套深度最多 32。超限、`NaN`、`Infinity` 和解析异常都变成 `rejected`，不调用 handler。

验证：

```text
.\.venv\Scripts\python.exe -m pytest tests/agents/test_executor.py tests/agents/test_policy.py -q --tb=short
54 passed in 2.23s

.\.venv\Scripts\python.exe -m pytest -q --tb=line
111 passed, 4 skipped in 2.95s
```

## M3.1 至 M3.3 — Provider、上下文、循环

新增：

- `app/agents/provider_adapter.py`
- `app/agents/context.py`
- `app/agents/loop.py`
- `tests/agents/test_provider_adapter.py`
- `tests/agents/test_context.py`
- `tests/agents/test_loop.py`

适配层用 `asyncio.timeout` 包住现有 `create_chat_completion`。预算是模型超时和剩余墙钟的较小值，因此 Provider 内部重试也不能超过这次预算。现有 Provider 签名没有改。

上下文保留 system 和当前用户消息，只按完整工具组裁剪。`reasoning_content` 可以留在下一次模型请求里，公开消息会去掉它。

循环按顺序执行工具。重复或非法 `tool_call_id` 在执行前失败。`length`、过滤、中断、资源不足和未知 `finish_reason` 都是失败，不是完成。

这三段的专项结果：

```text
.\.venv\Scripts\python.exe -m pytest tests/agents/test_provider_adapter.py tests/agents/test_context.py tests/agents/test_loop.py -q --tb=short
26 passed in 0.36s
```

这三段没有各自再跑一遍全量。全量结果在文末。

## M3.4 至 M3.6 — Runtime、事件、API

新增：

- `app/agents/events.py`
- `app/agents/task_manager.py`
- `app/agents/runtime.py`
- `app/api/v1/endpoints/agents.py`
- `app/schemas/agents.py`
- `tests/agents/test_events.py`
- `tests/agents/test_runtime.py`
- `tests/api/test_agent_api.py`

同时接入 `app/main.py`、`app/api/v1/router.py`、`app/api/deps.py`、`app/core/config.py`、`app/core/exceptions.py` 和 `.env.example`。

Runtime 使用 `server_policy_from_settings`，再对请求做收紧合并。同步工具进入运行时持有的 `ThreadPoolExecutor`，大小来自 `AGENT_SYNC_TOOL_CONCURRENCY`。超时返回后，线程仍占着池内工人，直到函数返回。

任务表在进程内存中。终态只写一次。取消会取消运行协程，并最多等待 `AGENT_CANCEL_CLEANUP_SECONDS`。SSE 断开只退订，不取消任务。

事件队列有上限。进度事件可以丢弃并计数；终态保留在任务查询里。载荷只允许步数、工具名、状态、错误码、结束原因和丢弃计数。

专项结果：

```text
.\.venv\Scripts\python.exe -m pytest tests/agents/test_events.py tests/agents/test_runtime.py -q --tb=short
8 passed in 0.43s
```

API 用例包含在下面的全量结果中。第一次 API 运行时，事件丢弃计数和线程槽断言各有一个失败，已按实现修正后重跑。

## 最终全量

```text
.\.venv\Scripts\python.exe -m pytest -q --tb=line
150 passed, 4 skipped in 4.31s
```

失败数为 0。4 个跳过仍是 `tests/test_live_deepseek.py` 中 `DEEPSEEK_LIVE != 1` 的真实联调，本次没有执行。

`git diff --check` 没有报告空白错误。

## 风险

- 同步线程不能被强制停止。并发上限只限制同时占用的工人数量。
- 第一阶段 `ToolService` 仍保留原来的字符串截断回退。Agent 路径已经分开处理。
- 任务不跨进程，也不在重启后保留。

## 真实联调与复核

时间：2026-11-06。模型由 Live 测试固定为 `deepseek-flash`，Base URL 为 `https://api.deepseek.com`。密钥只从本机 `.env` 载入当前进程，没有写入仓库或日志。

补充了两条 Live 测试，原有四条没有改跳过条件：

- `test_live_agent_tool_round_trip`：Agent 调用 calculator，工具结果回到模型，最终答复包含 437。
- `test_live_agent_deadline_stops_the_run`：墙钟 0.05 秒时任务进入 `limit_reached`，错误码为 `AGENT_DEADLINE_EXCEEDED`。

真实联调命令：

```text
$env:DEEPSEEK_LIVE="1"
.\.venv\Scripts\python.exe -m pytest -q --tb=short
158 passed in 12.99s
```

失败数为 0，跳过数为 0。其中 6 条是真实 API 测试，其余为离线测试。随后清除临时环境变量，再跑离线全量：

```text
.\.venv\Scripts\python.exe -m pytest -q --tb=short
152 passed, 6 skipped in 6.14s
```

6 个跳过全部是 `DEEPSEEK_LIVE != 1`。`git diff --check` 通过。

402 复核：`map_provider_exception` 仍对第一阶段返回 `UPSTREAM_ERROR` / 502，不重试。映射后的异常带上 `upstream_status=402`，Agent 适配层据此归类为 `balance` / `UPSTREAM_BALANCE`。

TaskManager 仍是进程内存、TTL 清理、活动上限和一次终态。同步工具默认 `max_workers=2`。超时后的线程继续占用工人，迟到结果不会写成成功。取消传播 `CancelledError`。第一阶段聊天、Schema、错误码、ToolService 和 SSE 事件名没有替换。
