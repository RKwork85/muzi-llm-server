# Phase 2 实施计划（M0）

核查日期：2026-11-06。本文件只规划，不授权直接开始写生产代码。M1 必须等人工确认。

Phase 2 在冻结的 v0.1.0 之上增加进程内 Harness / Agent Runtime。不重写 Provider，不改变 Phase 1 路由、Schema、错误码和 SSE 事件名。不引入 LangChain、LangGraph、Redis、RQ、Celery、数据库、MCP 或 RAG。

## 1. 目标架构

```text
POST /api/v1/agents/run
POST /api/v1/agents/run/stream
        |
        v
AgentRuntime ---- TaskManager
        |
        v
AgentLoop ---- EventPublisher
   |    |
   |    +---- ContextManager
   |    +---- ExecutionPolicy
   |    +---- ToolExecutor ---- 现有 ToolRegistry ---- calculator / get_current_time
   |
   +---- 现有 DeepSeekProvider.create_chat_completion
   +---- 现有 PromptService
```

API 层可以依赖 Runtime。Runtime、Loop、Executor 不导入 FastAPI 的 `Request` 或 `Response`。

建议目录：

```text
app/agents/
  runtime.py
  loop.py
  policy.py
  context.py
  task_manager.py
  events.py
  executor.py
  models.py
app/schemas/agents.py
app/api/v1/endpoints/agents.py
tests/agents/
```

不新建第二个工具实现，也不把 Phase 2 逻辑塞进 `ChatService.run_tool_loop`。那个方法是 Phase 1 的 HTTP 工具循环，异常会变成现有错误码。Agent 需要不同的失败语义，应走新循环。

## 2. 复用、新增和适配

可以直接复用：

| 现有代码 | Phase 2 用法 |
|---|---|
| `DeepSeekProvider.create_chat_completion` | AgentLoop 的模型调用 |
| `ProviderChatRequest`、`CompletionResult`、`ToolCall` | 循环内部的 Provider 边界 |
| `PromptService.render` | 每个 run 的系统提示词 |
| `ToolRegistry`、`build_default_registry`、两个 handler | 唯一的工具实现和权限来源 |
| `parameters_schema` | 交给模型的 function schema |
| `format_sse` | 新事件名的帧编码 |
| `AppError` / `error_payload` | 新增码使用同一信封 |
| `get_request_id`、日志过滤、lifespan | 资源和请求标识 |
| `FakeProvider` | 测试里的模型替身 |

需要适配，不能直接调用：

| 现有接口 | 差异 | 适配方式 |
|---|---|---|
| `ToolService.execute` | 失败即抛错并中止整批；一次最多 5 个；没有单次状态 | 新 `ToolExecutor` 调用 registry 和 handler，返回成功或拒绝结果。不改 `ToolService` |
| `StreamChunk` | 不包含 tool call 增量 | M4 只用非流式补全。M6 不伪造 `agent.model.delta` |
| `LLMProvider` | 没有 deadline 参数 | Loop 用 `asyncio.timeout` 包住单次模型调用，不改 Provider 签名 |
| `ToolSpec.risk_level="low"` | Skill 草案写的是 `read/write/external` | 保持 Phase 1 字段。Phase 2 把现有 `low` 视为可执行的只读工具，不改注册表 |
| `ChatService._assistant_message` | 私有函数，`content or ""` | Loop 自己组装 assistant 消息，有工具且思考开启时保留 `reasoning_content` |

需要新增：

- `AgentRuntime`、`AgentLoop`、`ContextManager`、`ExecutionPolicy`
- `TaskManager`、`EventPublisher`
- `ToolExecutor`
- Agent Schema、两个新路由、Agent 错误码
- `tests/agents/`

预计修改的现有文件：

| 文件 | 时机 | 修改内容 |
|---|---|---|
| `app/main.py` | M5/M6 | lifespan 增加 Agent 组件；不新建第二个模型客户端 |
| `app/api/v1/router.py` | M6 | 挂载 agents 路由 |
| `app/core/exceptions.py` | M1 或 M6 | 只追加 Agent 错误类，不改旧码的含义和状态码 |
| `app/core/config.py`、`.env.example` | M7，若 M1 已需要默认值则提前到 M1 | 只增加有默认值的上限。`make_settings()` 已显式传入旧字段，新增默认值不会破坏现有测试 |
| `README.md` | M7 | 补充 Phase 2 的范围、示例和内存任务限制 |

M0 不修改以上文件。`tests/conftest.py` 只有在新增必填配置时才需要改；计划是所有新配置都有默认值。

明确不改：

- 六条 Phase 1 路由和它们的请求、响应模型
- `delta`、`usage`、`error`、`done` 的名称和顺序
- 现有错误码
- `calculator` 与 `get_current_time` 的实现和注册条件
- `parse_stream_chunk` 的现有丢弃行为

## 3. 模块职责

### ExecutionPolicy

服务器上限、配置默认值和请求值取最小。请求只能收紧，不能放宽，也不能加入未注册工具。

建议初始上限：

| 字段 | 建议默认 | 硬上限 | 理由 |
|---|---|---|---|
| `max_steps` | 4 | 8 | 覆盖一次工具调用加最终回答，并留有余量 |
| `max_tool_calls` | 3 | 8 | 接近 Phase 1 的 3 轮默认 |
| `max_tool_calls_per_step` | 3 | 5 | 解析器超过 5 个调用会直接报 `INVALID_MODEL_RESPONSE` |
| `max_wall_time_seconds` | 30 | 120 | 必须小于或等于可配置的模型超时累加上限 |
| `model_timeout_seconds` | 沿用 `DEEPSEEK_TIMEOUT_SECONDS` | 不超过该配置 | Provider 客户端超时仍是 60 秒默认 |
| `tool_timeout_seconds` | 沿用 2 秒 | 不超过 `TOOL_TIMEOUT_SECONDS` | 不放宽 Phase 1 |
| `max_context_messages` | 20 | 40 | 低于 Phase 1 客户端消息上限 50 |
| `max_context_chars` | 32000 | 64000 | 没有 tokenizer，按字符预算 |
| `max_tool_result_chars` | 沿用 4000 | 不超过 20000 | 与 Phase 1 截断上限一致 |
| `max_event_queue_size` | 100 | 500 | 队列满时失败，不无限增长 |
| `allowed_tools` | 两个已注册 low 工具 | 注册表子集 | 请求省略时用这个默认集 |

省略 `allowed_tools` 时允许当前两个低风险工具。请求里的名单必须是这个集合的子集，未知名称返回 `AGENT_TOOL_NOT_ALLOWED`，并且不向模型展示该工具。

### ToolExecutor

按精确名称查找 `ToolRegistry`。校验 run 级名单和 `risk_level=="low"`。严格 JSON 对象、拒绝多余字段。同步 handler 继续用 `asyncio.to_thread`。超时取工具超时和 run 剩余时间的较小值。输出复用 Phase 1 的 JSON 信封和截断规则，但是返回类型化结果：`succeeded`、`failed`、`timed_out`、`rejected`。不重试，不并行。

未知工具、坏参数和超时不抛到 HTTP 层，而是写入对应的 `role=tool` 失败结果，让 Loop 决定继续还是终止。handler 抛出的非 `CancelledError` 异常变成安全失败，不带 traceback。

### ContextManager

每个 run 独立。顺序是系统提示词、已有协议消息、当前用户消息。不接收客户端的 `system`、`tool` 或函数定义。

工具协议组是一条含 `tool_calls` 的 assistant 消息，加上下标一一对应的 `role=tool` 消息。裁剪时整组保留或整组去掉。不裁掉系统提示词和最新用户消息。无法在预算内保持这个不变量时，结果是 `AGENT_CONTEXT_LIMIT_REACHED`。不做模型摘要。字符数用 `json.dumps` 后的长度估算，不新增 tokenizer 依赖。

思考模式开启时，`reasoning_content` 只留在发给 Provider 的消息里。公开结果、事件和日志不序列化它。

### AgentLoop

```text
初始化上下文
for step in 1..max_steps:
    检查 deadline 和上下文
    create_chat_completion(只带允许的 tools, tool_choice=auto)
    无 tool_calls 且有正文 -> completed
    有 tool_calls:
        先检查本步数量和总数量，通过后才执行
        追加 assistant 消息
        按原顺序执行工具并追加 tool 消息
        继续
    二者都没有 -> failed / INVALID_MODEL_RESPONSE
预算用尽 -> limit_reached
```

解析器若因超过 5 个 tool call 抛出 `INVALID_MODEL_RESPONSE`，Loop 把它视为失败，而不是 `limit_reached`。

思考模式默认跟随 Phase 1：显式 `thinking.type=disabled`。这样 `tool_choice=auto` 可用，温度不会被上游忽略。请求不能在 Phase 2 自行打开思考模式，除非以后单独立项。内部仍按“如果结果里有 `reasoning_content` 就原样放回下一次请求”实现，避免配置被改成 enabled 后缺少协议字段。

### AgentRuntime 与 TaskManager

`task_id` 使用 `secrets.token_urlsafe`。状态只允许：

```text
created -> running
running -> completed | failed | cancelled | limit_reached
```

终态不可覆盖。一个 run 只有一个终态。任务表有最大条数和 TTL，进程退出即消失，不提供跨 worker 查询接口。

客户端断开取消 run。`CancelledError` 在清理后继续抛出。shutdown 取消仍归本进程拥有的活动任务。不在请求结束后留下无人等待的后台任务。

### EventPublisher

事件信封：

```json
{
  "request_id": "...",
  "task_id": "...",
  "sequence": 1,
  "timestamp": "2026-11-06T00:00:00Z",
  "type": "agent.started",
  "data": {}
}
```

`sequence` 从 1 递增。时间使用 UTC，格式 `%Y-%m-%dT%H:%M:%SZ`。队列有上限，满时终止为 `AGENT_EVENT_OVERFLOW`，不丢弃中间事件后假装成功。

事件名：

- `agent.started`
- `agent.step.started`
- `agent.tool.started`
- `agent.tool.completed`
- `agent.tool.failed`
- `agent.completed`
- `agent.failed`
- `agent.cancelled`
- `agent.limit_reached`

不实现 `agent.model.delta`。当前流式解析不能安全表达工具轮次中的 token。

`data` 只含步骤号、工具名、耗时、状态、公开回答和错误码。不含思维链、密钥、traceback、原始工具参数和未截断的工具输出。

顺序是 `agent.started`，然后若干步骤和工具事件，最后恰好一个终态事件。断开后不再向已关闭的连接写入。

### Agent API

`POST /api/v1/agents/run`

```json
{
  "message": "请调用 calculator 计算 23 * 19。",
  "prompt_name": "calculator",
  "allowed_tools": ["calculator"],
  "policy": {"max_steps": 4, "max_tool_calls": 3}
}
```

```json
{
  "task_id": "...",
  "request_id": "...",
  "status": "completed",
  "answer": "...",
  "steps": 2,
  "tool_calls": 1,
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
}
```

`message` 非空且有长度上限。`prompt_name` 必须是已有模板。`allowed_tools` 只收注册表中的名字。`policy` 中的数值必须为正，且不超过服务器上限。客户端不能提交工具 schema、handler、`system` 或 `tool` 消息。

`POST /api/v1/agents/run/stream` 使用 POST 和 `text/event-stream`。浏览器 `EventSource` 不能携带任意 POST 正文，客户端用 `fetch` 读取流。断开即取消。

新增错误码，HTTP 状态跟随现有风格：

| 码 | 建议状态 | 何时使用 |
|---|---|---|
| `AGENT_TOOL_NOT_ALLOWED` | 403 | 请求名单里有未知或未授权工具 |
| `AGENT_STEP_LIMIT_REACHED` | 422 | 终态 `limit_reached` |
| `AGENT_TOOL_CALL_LIMIT_REACHED` | 422 | 终态 `limit_reached` |
| `AGENT_DEADLINE_EXCEEDED` | 504 | 终态 `limit_reached` |
| `AGENT_CONTEXT_LIMIT_REACHED` | 422 | 终态 `limit_reached` |
| `AGENT_CANCELLED` | 499 不使用；流内事件 | 标准 HTTP 没有稳定的 499 契约。非流式若在返回前取消，用 500 不安全。流式只发 `agent.cancelled`，不再补一个成功 JSON |
| `AGENT_EVENT_OVERFLOW` | 500 | 内部容量耗尽 |
| `AGENT_INVALID_STATE` | 500 | 非法状态迁移 |

Phase 1 的 `TOOL_NOT_ALLOWED`、`TOOL_ROUND_LIMIT` 保持原样。Agent 超限不复用 `TOOL_ROUND_LIMIT`，避免改变旧接口的含义。

非流式 run 在循环结束后返回一个 JSON 终态。流式 run 的终态只出现在 SSE 里。

## 4. 与 Phase 1 的兼容策略

- 新路由只追加到 `app/api/v1/router.py`。
- Agent 使用新的 Pydantic 模型，不继承后修改 `ChatCompletionRequest`。
- 模型调用继续走现有 Provider。测试用现有 `FakeProvider`，必要时给 Fake 增加可选脚本，不改变它已有的默认行为。
- 工具实现仍由 `build_default_registry()` 提供。Phase 2 与 Phase 1 在同一次 lifespan 中共享同一个 registry 实例。
- 新配置项都有默认值，旧环境变量继续有效。
- 每个里程碑先跑新测试，再跑 `.\.venv\Scripts\python.exe -m pytest -q`。Phase 1 的 57 个通过项必须保持通过，4 个 live 测试保持默认跳过。

## 5. M1 到 M7

### M1 — 领域模型与策略

新增 `app/agents/models.py`、`app/agents/policy.py`、`tests/agents/test_policy.py`。若错误码要被策略测试引用，只在 `exceptions.py` 追加类。

测试：默认值安全；0 和负数拒绝；请求不能超过上限；未知工具不能通过请求加入；硬上限生效。

不调用 Provider，不改路由。

### M2 — 执行器

新增 `app/agents/executor.py`、`tests/agents/test_executor.py`。使用现有 registry 和 handler。

测试：合法参数只执行一次；非法 JSON、非对象、未知字段、未知工具、不在名单中的工具；超时；同步 handler 不阻塞事件循环；异常不含 traceback；超长结果仍是 JSON；不重试。

现有 handler 都是同步函数。异步 handler 用一个测试专用协程验证，不改 `calculator`。

### M3 — 上下文

新增 `app/agents/context.py`、`tests/agents/test_context.py`。

测试：系统提示词保持第一条；最新用户消息保留；消息数和字符预算；工具组整组保留；没有孤立 tool 消息；无法安全裁剪时返回上下文超限；公开序列化不含 `reasoning_content`。

### M4 — 循环

新增 `app/agents/loop.py`、`tests/agents/test_loop.py`。使用 `FakeProvider`。

测试：无工具直接完成；一次工具后给出最终答案；多个工具保持 id 和顺序；未知工具；坏参数；空响应；步骤上限；工具总数上限；单步上限；deadline；Provider 超时和认证错误；取消；有 `reasoning_content` 时下一次请求仍带该字段，且公开结果不带。

### M5 — Runtime、任务和事件

新增 `runtime.py`、`task_manager.py`、`events.py` 及对应测试。

测试：合法与非法迁移；终态不可变；恰好一个终态；序号递增；队列上限；载荷过滤；任务表容量或 TTL；取消后不是成功。

此阶段可以把组件放进 lifespan，但可以还没有 HTTP 路由。若放进 lifespan，必须确认 Phase 1 的启动和关闭测试仍然通过。

### M6 — API 与 SSE

新增 `app/schemas/agents.py`、`app/api/v1/endpoints/agents.py`，修改 router 和 lifespan 装配。测试放在 `tests/agents/test_agent_api.py` 和 `test_agent_stream.py`。

测试：两条新路由的成功路径；校验失败；未知提示词；不允许的工具；政策放宽被拒绝；错误信封和 request_id；SSE 帧和恰好一个终态事件；断开后取消；Phase 1 六条路由的响应形状不变。

不实现 `agent.model.delta`。

### M7 — 收尾

更新 README、`.env.example` 和 `docs/phase2/SECURITY_REVIEW.md`。补安全回归：提示词不能注册工具；`shell` 被拒绝；工具结果中的伪造指令不改变名单；日志不含密钥和思维链；取消不会报成功。

可选的真实联调继续用 `DEEPSEEK_LIVE=1`，默认不跑。至少包含无工具回答和一次 calculator。不把 Key 写入仓库。

## 6. 官方文档与代码差异

2026-11-06 重新读取了 DeepSeek Tool Calls、Chat Completions、Thinking Mode 页面。

与当前实现一致的部分：

- base URL 是 `https://api.deepseek.com`，模型 ID 包含 `deepseek-flash` 和 `deepseek-v4-pro`。
- 非思考模式的工具示例使用 `extra_body={"thinking":{"type":"disabled"}}`。本服务默认如此。
- 工具由应用执行，模型只提议调用。
- 结果消息是 `role=tool`、`tool_call_id` 和 `content`。
- 带 `tools` 的后续请求必须回传此前的 `reasoning_content`，否则官方说明会返回 400。
- `tool_choice=auto` 是有工具时的默认值。`required` 和具名选择在思考模式下返回 400。
- `max_tokens` 范围是 1 到 393216。
- `reasoning_effort` 的 API 参考值为 `none`、`low`、`high`、`max`。`none` 表示关闭思考。这与 Phase 1 Schema 一致。
- `strict: true` 属于 Beta，要使用 `https://api.deepseek.com/beta`。Phase 2 不启用。
- Chat Completions 不支持把不是模型生成的 tool call 插入历史。Phase 2 不合成工具调用。

需要记住的差异：

- Thinking Mode 指南里的 OpenAI 格式表格把 `reasoning_effort` 写成 `low/high/max`，把 `none` 放在 Responses API。Chat Completions API 参考明确列出 `none`。以 API 参考为准，不修改 Phase 1 已接受的 `none`。
- 思考模式下温度会被忽略且不报错。Phase 2 默认关闭思考，避免温度和工具选择行为变化。
- 官方示例直接追加 SDK message 对象。本服务在 `content is None` 时写成 `""`。思考模式关闭时，真实联调已经成功。思考模式开启时，空字符串是否始终被接受，当前 live 测试没有覆盖。
- 官方示例的 tool call id 为 `call_00_kw66qNnNto11bSfJVIdlV5Oo`，符合现有 `^[A-Za-z0-9_-]{1,128}$`。这不能证明所有未来 id 都符合该正则。
- 本轮没有重新阅读 OpenAI SDK 3.46 的请求变换源码。Phase 1 把 `reasoning_content` 作为普通字典键送入 `messages`。M4 要用 Fake 断言该键确实出现在待发送消息中。若以后升级 SDK，需要重新确认未知键不会被删除。

FastAPI lifespan、Python `asyncio`、Pydantic v2 和 SSE：

- 现有 `lifespan` 符合 FastAPI 共享资源的启动和关闭方式。Phase 2 继续使用它。
- Python 3.13 中 `CancelledError` 继承 `BaseException`。Provider 的 `except Exception` 不会吞掉它。工具层的 `wait_for` 超时在 3.11+ 会表现为 `TimeoutError`，现有代码已经捕获。
- Pydantic 2.14 的 `model_validate` 和 `extra="forbid"` 已经用于工具参数。Phase 2 继续用这个方式，不新增依赖。
- WHATWG SSE 以空行分发事件，数据是 UTF-8。`format_sse` 已产生 `event`、一行 `data` 和空行。Phase 2 复用它，只更换事件名。
- OWASP LLM Top 10 的约束与本计划一致：提示词不是授权边界，工具权限、参数校验、输出上限和代理步数都由代码执行。不宣称能够消除提示词注入。

## 7. 风险

1. Provider 重试可能让单步模型调用超过 Agent 墙钟。M4 必须用剩余 deadline 包住调用，并测试超时后不再继续下一轮。
2. `to_thread` 里的同步计算在取消后可能仍跑到结束。计算器没有外部副作用，Phase 2 可以接受“取消后线程自然结束”，但不能把这种 run 标记为成功。
3. 解析器的 5 个 tool call 上限先于策略发生。默认单步上限保持不超过 5，避免两套限制含义冲突。
4. 思考模式加工具的协议回传还没有真实联调。默认关闭思考可以绕开这个风险。一旦允许启用，需要单独的 live 测试。
5. 事件队列和任务表若没有上限，一个慢客户端会积累内存。M5 要把上限写成测试。
6. 把 Agent 装配进 lifespan 时，不能让缺少 Agent 配置阻止 Phase 1 启动。
7. `qs_llm_dp` 尚未进入 Git。实施前应确认哪些文档和 `.env` 会被提交。`.env` 必须继续忽略。

## 8. 是否可以开始 M1

可以开始，但只限于 M1 的模型和策略测试。条件是本计划和 `BASELINE.md` 先经过人工确认。

当前没有阻塞 M1 的未知生产接口：策略模块不需要新依赖，也不需要改 Provider。M4 之前不处理思考模式开启后的真实 400 风险，因为默认策略继续关闭思考。
