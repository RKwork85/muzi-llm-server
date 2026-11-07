---
name: deepseek-fastapi-agent-runtime-phase2-m3
version: 3.0.0
description: 在既有 DeepSeek FastAPI 项目中，按已审查的 M1/M2 契约，分阶段实现可测试、可取消、有资源边界的 Agent Runtime；严格兼容官方 DeepSeek API。
---

# Skill 3 — DeepSeek FastAPI Agent Runtime（M3 实施版）

## 0. 使命与执行规则

你是本项目的资深 Python/FastAPI Agent Runtime 工程师。你的任务是**按本 Skill 直接实现 M3**，不是重新做架构设计，也不是只输出建议。

唯一项目根目录：

`C:\Users\rkwork\Desktop\muzihhh\work\muzi_project\qs_llm_dp`

必须先确认当前工作目录、Git 分支、工作区状态、现有文件和测试。禁止访问旧项目路径。

### 已知基线

- Phase 1 基线提交：`7063d99`，Tag：`v0.1.0-phase1-baseline`。
- M1：Agent Models、ExecutionPolicy、RunClock。
- M2：ToolExecutor。
- M1/M2 审查报告：`docs/phase2/REVIEW_REPORT.md`。
- 审查结论 C：可进入 M3 实施；审查未发现 P0/P1。
- 审查时全量测试：104 passed、4 skipped、0 failed；4 个 skip 是默认不执行的真实 DeepSeek live tests。
- 当前已知 P2：
  1. 小结果上限和较长工具名组合时，成功截断可能产生非法 JSON，但状态仍是 succeeded。
  2. 深层 JSON 可触发 RecursionError，工具参数没有足够的输入长度/嵌套深度限制。
- 已接受的运行时事实：同步 handler 在线程中超时/取消后，线程不能被 asyncio 强制杀死；迟到结果必须丢弃，且必须限制线程并发和资源占用。

以上是已知基线，不代表当前工作区必然干净。必须现场检查，不得假设。

### 不可违反的工程原则

1. 先阅读真实代码和 `REVIEW_REPORT.md`，再修改；不得凭 Skill 猜测项目接口。
2. 复用现有 `DeepSeekProvider`、`ToolRegistry`、`ExecutionPolicy`、`RunClock`、`ToolExecutor`、Pydantic 模型和项目配置。
3. 不重写第一阶段，不破坏既有 API、Schema、错误码、SSE 契约和测试。
4. 不引入第二套 Provider、工具注册表、策略模型或平行的执行体系。
5. 所有新增限制必须服务端强制执行；请求不能放宽策略。
6. 总 Deadline 必须贯穿 Runtime、Loop、Provider 和 ToolExecutor；不能被重试、排队、事件发布或工具调用绕过。
7. 取消是控制流，不得吞掉 `asyncio.CancelledError`；终态只能完成一次。
8. 模型输出、工具参数、工具结果、事件载荷均为不可信输入/数据。
9. 不记录 API Key、完整敏感参数、内部堆栈或未经脱敏的模型思考内容。
10. 不使用 `eval`、`exec`，不执行模型生成的 Python/命令。
11. 不为“看起来完整”而过度设计；优先实现可测试的最小闭环。
12. 不虚构 DeepSeek API 能力。所有 Provider 请求字段和响应解析必须以本 Skill 的官方文档参考及当前项目已用 SDK 版本为准。

## 1. DeepSeek 官方 API 兼容基线（必须遵守）

官方文档入口：<https://api-docs.deepseek.com/zh-cn/>

本项目现有 Provider 若使用 OpenAI SDK 的 Chat Completions 格式，默认沿用这一协议，不得擅自切换到 Responses API 或 Anthropic API。

### 1.1 Chat Completions

- OpenAI 兼容 Base URL：`https://api.deepseek.com`
- 端点：`/chat/completions`
- 使用项目配置中的模型名；不得硬编码过时模型名。官方当前文档示例使用 `deepseek-flash`，可用模型以官方模型文档和账户实际可用范围为准。
- 多轮对话必须由客户端在每次请求中传递完整 messages 历史；不要假设服务端保存会话。
- 非流式与流式响应都必须检查 choices、message/delta、finish_reason 和错误。
- 流式 SSE 以 `data: [DONE]` 结束；必须识别连接中断、缺少结束标记和不完整响应。
- `stream_options.include_usage` 只有在 `stream: true` 时才能使用；usage 可能只出现在结束前最后一个 chunk。
- `finish_reason` 至少正确处理 `stop`、`tool_calls`、`length`、`content_filter`、`insufficient_system_resource`、`aborted`；未知值必须安全失败或归类，不能误判为正常完成。

### 1.2 Tool Calls

- 模型只提出工具调用意图；模型本身不会执行本地工具。
- 服务端必须通过既有 ToolRegistry 和 ToolExecutor 执行，模型不能传入 handler、任意工具定义或任意代码。
- 标准循环：请求模型 → 收到 assistant tool_calls → 将 assistant 消息追加到 messages → 逐个/受控并发执行 → 为每个调用追加 `role="tool"`、匹配 `tool_call_id` 的结果 → 再请求模型。
- 不能遗漏 tool_call_id 对应关系；一个 assistant 消息中的每个工具调用都必须有对应结果，或在失败/取消策略中形成明确终态。
- DeepSeek Chat Completions 不支持在对话历史中间任意插入非模型生成的 tool call；不要用这种方式伪造历史。只按官方标准 assistant tool_calls / tool result 顺序构造历史。
- 不启用 Beta `strict`，除非当前项目明确配置、官方支持范围已核实、并有兼容测试。默认使用普通 function schema，由本地 Pydantic 校验兜底。

### 1.3 Thinking Mode 与 reasoning_content

- 不得未经检查就开启 thinking mode。
- 若现有 Provider/配置启用了 DeepSeek thinking mode，必须按官方文档保留并在后续请求中正确回传该轮 assistant 消息的 `reasoning_content`，尤其是带 tools 的请求；否则可能收到 400。
- 不得把 reasoning_content 作为面向用户的输出或普通日志内容。
- 若当前 SDK/模型返回字段无法可靠保留，应先采用项目已验证的非思考工具调用路径，或明确阻断并报告；不能静默丢弃字段后继续。
- 不自行设置文档未支持的参数；未知参数不能靠“OpenAI 兼容”推断支持。

### 1.4 官方错误与重试

- 400/422：请求格式或参数错误，默认不重试。
- 401：认证失败，不重试；安全记录错误类别，不记录密钥。
- 402：余额不足，不重试。
- 429：限速/并发达到上限；仅在总 Deadline 和重试预算允许时做有界退避。
- 500/503：服务端故障/繁忙；仅在总 Deadline 和重试预算允许时做有界退避。
- 不得对工具执行做自动重试，除非工具明确声明幂等且有单独设计；M3 默认不重试工具。
- Provider 的重试总时间必须计入 RunClock；禁止 SDK 内部无限/超预算重试。

详细来源见 `references/DEEPSEEK_API_CONTRACT.md`。

## 2. M3 目标与模块边界

实现完整最小闭环：

`Agent API → TaskManager/Runtime → ContextManager → AgentLoop → DeepSeekProvider → ToolExecutor → ContextManager → AgentLoop → AgentResult/EventPublisher → API/SSE`

模块职责：

### ContextManager
- 将用户输入、服务端 Prompt、历史消息和工具结果构造成模型 messages。
- 维护合法 Chat Completions 消息顺序。
- 限制消息数量、字符数；遵循 `ExecutionPolicy.max_context_messages`、`max_context_chars`。
- 采用确定性裁剪：优先保留 system/developer 约束、当前用户请求和最近必要的工具调用闭环；不能留下没有 tool result 的 assistant tool_calls。
- 工具结果必须按 tool_call_id 配对。
- 不将 reasoning_content 暴露给用户或事件流。
- 若不能安全裁剪完整工具调用闭环，应明确失败，不得静默生成非法历史。

### AgentLoop
- 只负责模型-工具决策循环，不管理 HTTP、SSE 连接、任务存储或全局生命周期。
- 每一步先检查总 Deadline、最大步骤、总工具次数、每步工具次数。
- 解析 finish_reason 和 assistant message。
- `tool_calls` 时校验每个调用并交给 ToolExecutor；工具结果回填后进入下一轮。
- `stop` 且有可用最终文本时完成；`length`、过滤、中断、资源不足、未知 finish_reason 不得伪装成成功。
- 工具调用并发必须受策略与 Runtime 并发闸门共同限制；如果项目无安全的并发工具语义，先顺序执行。
- 模型没有工具调用但没有最终文本时，按明确失败策略处理，禁止无限循环。

### AgentRuntime
- 每次运行的唯一协调者：生成/接收 run_id，构造 ExecutionPolicy、RunClock、ContextManager、AgentLoop。
- 管理 created → running → terminal 状态。
- 统一捕获普通异常并转换为安全 AgentResult；重新抛出 CancelledError。
- 终态只能设置一次；迟到 Provider/线程/事件不得覆盖终态。
- 负责全链路 Deadline、资源闸门、事件生命周期和清理。
- 不承载消息裁剪细节、HTTP 路由细节或 SSE 编码细节。

### TaskManager
- 负责任务注册、查询、取消、清理和状态快照。
- 首版可使用进程内内存管理；必须明确多 worker/进程重启后不持久化的限制。
- 不宣称内存任务可跨进程查询。
- 任务取消必须取消运行协程并等待有限清理时间；线程任务不能强杀。
- 对终态和重复取消/重复完成保持幂等。
- 对活动任务数量、保留时间和清理策略设置服务端上限。
- Redis 持久化作为后续扩展，不在没有现有依赖/授权时擅自引入。

### EventPublisher
- 统一事件 envelope，复用既有 `EventEnvelope` 和 `max_event_queue_size`。
- 事件种类至少覆盖 run_started、step_started、model_request_started、model_response_received、tool_call_started、tool_call_completed、run_completed、run_failed、run_cancelled、limit_reached。
- 事件只含必要、脱敏、有限长度数据；不得发布 API Key、完整 reasoning_content、内部堆栈或不受限工具参数。
- 有界队列；Agent 核心执行不得被慢速 SSE 消费者无限阻塞。
- 定义队列满策略：可丢弃低优先级进度事件并计数；终态事件必须有可靠交付/可查询状态兜底。禁止无界缓存。
- SSE 断开不应自动取消 Agent 任务，除非 API 明确提供单独的 cancel 语义。
- SSE 编码、心跳、断连清理、终态结束标记需与现有项目约定兼容。

## 3. 实施顺序（必须逐阶段完成）

不得跳阶段，不得一次性生成所有代码后不验证。每阶段完成后运行专项测试、全量测试，并更新实施记录。

### M3.0 — M1/M2 P2 加固（Runtime 前置）
1. 阅读审查报告和 executor/policy/models 全部实现。
2. 修复成功截断可能破坏 JSON 的问题：不得简单切字符串后仍标 succeeded。可选择安全结构化截断并重新序列化；若无法保证合法 JSON，返回明确失败/截断状态，保持结果契约一致。
3. 为工具参数 JSON 增加原始字节/字符长度上限、嵌套深度限制和安全解析；捕获 JSONDecodeError、RecursionError、Unicode/类型边界异常。
4. 保持 calculator/get_current_time 行为兼容。
5. 新增边界测试：最小结果上限、长工具名、深层 JSON、超长参数、NaN/Infinity、非法 JSON、截断后可 json.loads。
6. 不放宽任何 ExecutionPolicy 上限；不增加不必要依赖。

验收：所有新增测试通过，M1/M2 专项测试通过，全量回归无新增失败。

### M3.1 — Provider 适配与 Deadline 约束
1. 先阅读现有 Provider、Settings、依赖版本和 live tests。
2. 设计最小内部模型响应适配层；不得让 AgentLoop 直接依赖 SDK 内部复杂对象。
3. 传入剩余 Deadline 作为请求 timeout；限制 SDK retries，使总重试不突破 RunClock。
4. 将 Provider 异常映射为内部安全错误类别，区分认证、参数、余额、限速、服务端繁忙、超时、网络中断。
5. 正确解析 assistant content、tool_calls、tool_call_id、function name/arguments、finish_reason、usage。
6. 如果使用 thinking mode，严格保留 reasoning_content 并在工具循环中回传；否则明确保持 thinking disabled/现有配置。
7. 单元测试全部 mock Provider，不依赖真实 API Key；live test 继续默认 skip。

验收：Provider timeout 不超过总剩余时间；模型错误不会泄露密钥；工具调用响应映射正确；原 Provider API 兼容。

### M3.2 — ContextManager
1. 实现消息构造、追加、配对校验、裁剪和字符/消息预算。
2. 保留 system Prompt 和当前请求。
3. 裁剪必须以完整消息组为单位，不能拆开 assistant tool_calls 与 tool results。
4. 不伪造模型生成的 tool_calls，不向 Chat Completions 历史中间插入伪造 tool_calls。
5. 保留必要的 reasoning_content（仅当 thinking mode 启用且 SDK 支持），但不对外发布。
6. 为空历史、超长用户输入、超长工具结果、消息数超限、字符数超限、孤立 tool result、缺失 tool result 编写测试。

验收：输出 messages 满足 DeepSeek Chat Completions 的角色/工具消息顺序；预算不超限；工具调用闭环完整。

### M3.3 — AgentLoop
1. 实现有限循环：每轮检查 Deadline、max_steps、max_tool_calls。
2. 生成模型请求时只暴露服务端注册且策略允许的工具定义。
3. 模型提出的工具名必须再经过 ToolExecutor 的权限验证；模型输出不是授权凭据。
4. 处理单轮多个 tool_calls；默认顺序执行，只有在隔离和取消测试证明安全后才启用受控并发。
5. 每个 tool_call_id 只执行一次；重复 ID、缺失 ID、非法 arguments 均拒绝或终止，不能重复副作用。
6. 对每个 tool_call 产生配对 tool message，保证下一轮 messages 合法。
7. 达到限制时返回 limit_reached；模型自然结束才 completed。
8. 对 `length`、`content_filter`、`insufficient_system_resource`、`aborted`、未知 finish_reason 做明确映射。
9. 不在 AgentLoop 内实现 API、SSE、持久化。

验收：覆盖纯文本回答、单工具、多工具、工具失败、未授权工具、达到步数、达到调用数、Deadline、模型中断、模型输出为空。

### M3.4 — AgentRuntime 与 TaskManager
1. Runtime 协调 Policy、Clock、ContextManager、AgentLoop、EventPublisher。
2. 明确状态转换表，所有终态只能发生一次。
3. TaskManager 实现创建、查询、取消、活动任务上限、TTL 清理。
4. 对取消传播 CancelledError；清理动作有时限。
5. 对同步工具线程使用有界容量/信号量；超时/取消后仍占用执行槽，直到线程真实结束，防止不断超时导致无限堆积线程。
6. 迟到结果不更新运行状态、不进入下一轮、不发布虚假的成功事件。
7. SSE 订阅取消与 Agent 任务取消分离。
8. 任务状态快照不暴露内部异常/敏感内容。

验收：状态机合法；并发任务隔离；重复取消/完成幂等；Deadline 和取消可靠；线程池/并发上限测试通过。

### M3.5 — EventPublisher 与 SSE
1. 实现有界事件发布接口与订阅接口。
2. 事件使用统一 EventEnvelope；校验事件类型、run_id、序号/时间戳字段。
3. 对载荷进行白名单过滤、长度限制和敏感信息脱敏。
4. 队列满时执行文档化策略，终态以 TaskManager 状态查询兜底。
5. 实现 SSE endpoint 时遵循现有 FastAPI 路由结构和项目响应风格。
6. 正确处理 `text/event-stream`、事件 ID（若设计支持）、心跳、客户端断开、终态结束。
7. 客户端断开默认只停止订阅，不取消 Agent。
8. 不改变第一阶段已有 SSE 路由契约。

验收：慢消费者不会阻塞 Agent；断连不泄漏订阅；队列有界；终态可查询；事件不含敏感字段。

### M3.6 — Agent API 与端到端验收
1. 新增独立版本化 Agent API，不修改既有 Chat Completions API。
2. 请求只接受用户消息、服务端 Prompt 名称和允许的策略子集；拒绝客户端传入工具实现、handler、任意工具 schema 或服务端配置。
3. 提供同步执行或任务式执行接口时，明确 API 语义；若支持异步任务，提供创建、查询、取消和 SSE 订阅接口。
4. 统一输入校验、错误码和响应 Schema；不要将内部异常直接返回。
5. 对 API 做 TestClient/ASGI 测试，Provider 使用 mock。
6. 端到端测试：用户请求 → 模型 tool call → 工具执行 → tool result → 模型最终答复；以及失败、超时、取消、限额、SSE 断连。
7. 验证所有旧路由、Schema、错误码、SSE 和测试保持兼容。

验收：Agent API 闭环可运行；安全/资源限制有效；全量测试通过；旧接口兼容。

## 4. 测试与质量门禁

每阶段必须：
- 新增正向、负向、边界、取消、并发测试。
- 不用 sleep 作为唯一同步手段；优先 Event/Barrier/Future 和可控 fake clock。
- 不依赖真实 DeepSeek 网络调用。
- 保留原有测试，不删除、不跳过、不降低断言。
- 运行相关专项测试和全量 `pytest -q`。
- 记录实际通过/失败/跳过数量和耗时。
- 检查 `git diff --check`、导入循环、类型和 lint（若项目已配置）。
- 测试失败必须定位修复，不能通过放宽断言掩盖。
- 不把静态推理说成测试通过。

全量测试命令（Windows PowerShell）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## 5. 目录和代码组织

先检查现有目录约定，再决定文件位置。优先使用：

```text
app/agents/
  __init__.py
  models.py
  policy.py
  executor.py
  context.py
  loop.py
  runtime.py
  task_manager.py
  events.py
  provider_adapter.py
app/api/v1/endpoints/
  agents.py
tests/agents/
tests/api/
docs/phase2/
  REVIEW_REPORT.md
  m3/
    IMPLEMENTATION_LOG.md
    ACCEPTANCE.md
```

这是建议布局，不是强制要求；若现有项目已有更合理的结构，遵循现有结构并在实施记录解释。

## 6. 配置和依赖

- 优先复用现有 Settings。
- 新配置必须有安全默认值、范围校验和文档。
- 不新增依赖，除非确有必要；新增前检查现有依赖是否已具备能力。
- 不将 API Key 写入代码、测试、日志或提交。
- 不随意修改 SDK 主版本。
- 所有并发数、队列长度、活动任务数、TTL、输入长度必须有硬上限。

## 7. Git 与工作区安全

开始前记录：
- `git status --short`
- `git branch --show-current`
- `git log --oneline -6`
- `git diff --stat`

若工作区有用户未提交修改，禁止覆盖；先识别并保留。不得访问旧目录。

禁止执行破坏性 Git 命令：`reset --hard`、`clean`、强制 checkout/restore、改写历史、移动既有 Tag。

不得擅自修改 `v0.1.0-phase1-baseline`。

除非用户明确授权，不创建提交、不推送、不改 Tag。每阶段结束提供变更文件清单和 diff 摘要，等待下一阶段指令；**但本 Skill 的自动执行目标是完成 M3 全部阶段，不要在阶段边界无故停下**。如果发现 P0/P1、安全风险、接口契约冲突或必须由用户决定的破坏性变更，立即停止并报告。

## 8. 文档交付

持续维护：

- `docs/phase2/m3/IMPLEMENTATION_LOG.md`：每阶段做了什么、文件变更、测试结果、风险。
- `docs/phase2/m3/ACCEPTANCE.md`：最终功能、兼容性、安全、测试验收。
- `docs/phase2/m3/DEVIATIONS.md`：与 Skill 不同的实现选择及原因（无偏差可写 none）。

不要只写“完成”；记录可复现的命令和真实结果。

## 9. 最终完成标准

全部满足才可报告 M3 完成：

1. ContextManager 消息构造、预算和裁剪正确。
2. AgentLoop 能完成真实模型协议下的有限工具调用循环。
3. AgentRuntime 全链路 Deadline、取消、异常和终态正确。
4. TaskManager 任务创建、查询、取消、清理可靠。
5. EventPublisher 有界、脱敏、不阻塞核心执行。
6. Agent API 和 SSE 可测试运行。
7. M1/M2 两项 P2 已修复并有回归测试。
8. 同步线程执行有并发硬上限，迟到结果不会污染状态。
9. DeepSeek Tool Calls/Thinking/finish_reason/stream 协议处理符合官方文档和当前 SDK 实际行为。
10. 全量回归测试通过；原有 skip 原因保持真实且透明。
11. 旧 API、Schema、错误码、SSE 契约兼容。
12. 文档、变更清单、测试记录齐全。

最终报告必须明确列出：
- 已实现模块；
- 新增/修改文件；
- API 路径与请求/响应示例；
- 配置项；
- DeepSeek 协议支持范围；
- 全量测试统计；
- live test 是否真实执行；
- 已知限制；
- 是否达到 M3 完成标准。

若任何标准未满足，不得声称 M3 完成。
