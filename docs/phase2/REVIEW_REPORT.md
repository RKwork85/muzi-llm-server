# Phase 2 M1 + M2 代码审查报告

审查日期：2026-11-06。审查方式为只读：阅读实现与现有测试、执行现有 pytest、执行只读 Git 命令，并对测试未覆盖的边界做了一次性只读探针。探针没有写入测试文件，也没有修改生产代码。探针结果不计入 pytest 通过数。

证据分四类：

| 标记 | 含义 |
|---|---|
| 已通过实际测试验证 | 现有 `tests/agents/` 用例在本次 pytest 中通过 |
| 已通过代码静态审查 | 由当前源码直接推出，没有另跑用例 |
| 只读探针 | 本次在解释器里调用现有函数观察到的结果，不是测试套件 |
| 尚未验证 | 没有测试，也没有探针 |

## 1. 审查范围

项目根目录：`C:\Users\rkwork\Desktop\muzihhh\work\muzi_project\qs_llm_dp`。

对照基线：`7063d99`（`v0.1.0-phase1-baseline`）。审查时 HEAD 为 `1f1751b`。

纳入审查的实现：

- `app/agents/__init__.py`
- `app/agents/models.py`
- `app/agents/policy.py`
- `app/agents/executor.py`
- `tests/agents/test_policy.py`
- `tests/agents/test_executor.py`

对照阅读的第一阶段集成点：

- `app/tools/registry.py`、`app/tools/schemas.py`、`app/tools/calculator.py`、`app/tools/clock.py`
- `app/services/tool_service.py`
- `app/core/config.py` 中与超时、结果长度相关的字段
- `docs/phase2/IMPLEMENTATION_PLAN.md`、`docs/phase2/BASELINE.md`

`app/providers/`、`app/api/`、`app/schemas/` 未逐行重审。它们是否被 M1/M2 改动，以 `git diff 7063d99..HEAD --name-only` 为准。

本次没有实现 ContextManager、AgentLoop、AgentRuntime、TaskManager、EventPublisher 或 Agent API。

## 2. 总体结论

M1 和 M2 的权限边界、策略收紧和 deadline 拦截是可靠的。请求不能放宽服务端数值上限，不能把未授权工具加进白名单，空名单会禁用全部工具。时间耗尽时执行器不会创建 handler 任务。工具超时后，迟到的线程结果不能把已返回的 `timed_out` 改成成功。取消一个调用不会改写另一个调用的结果。

当前两个内置工具 `calculator` 和 `get_current_time` 在合法结果上限下，成功截断仍能得到合法 JSON。这个结论有边界：工具名足够长且结果上限被收到 96 附近时，成功截断会切出非法 JSON，同时状态仍是 `succeeded`。深层 JSON 触发的 `RecursionError` 也不会被收成 `rejected`。同步 handler 在超时或取消后仍会把线程跑完，执行器没有并发上限。

没有发现 P0 或 P1。上述问题不破坏当前两个工具的权限判断，也不让超限请求静默放宽。

**准入结论：C。允许进入 M3 架构设计，不允许据此开始 M3 编码。**

M3 设计需要写明策略入口、`RunClock` 的调用方式、同步工具并发上限，以及参数大小限制。截断回退和解析异常逃逸应在 M3 编码前修掉。

## 3. M1 审查结果

M1 由 `models.py`、`policy.py` 和 `test_policy.py` 组成。模块只定义数据和策略，不调用 Provider，不导入 FastAPI。`app/agents/__init__.py` 只有模块说明。依赖方向是 `models` ← `policy` ← `executor`，`models` 不反向引用策略或注册表。静态审查未发现循环依赖。

| 检查项 | 结论 | 证据 |
|---|---|---|
| `AgentRequest` 拒绝工具定义、`thinking`、空消息和非法 `prompt_name` | 成立 | 已通过实际测试验证 |
| `RequestedPolicy` 使用 `extra="forbid"` 和 `strict=True`，拒绝 0、负数、布尔和数字字符串 | 成立 | 已通过实际测试验证 |
| 省略的策略字段保持服务端值 | 成立 | 已通过实际测试验证 |
| 大于服务端上限的字段被 `PolicyError` 拒绝 | 成立 | 已通过实际测试验证 |
| `ExecutionPolicy` 冻结，事后修改请求不会回写已合并策略 | 成立 | 已通过实际测试验证；赋值冻结另有只读探针 |
| `max_tool_calls_per_step` 不能大于 `max_tool_calls` | 成立 | 已通过实际测试验证 |
| 未知工具、服务端名单之外的工具、重复名、非法名被拒绝 | 成立 | 已通过实际测试验证 |
| 空 `allowed_tools` 得到空元组 | 成立 | 已通过实际测试验证 |
| 硬上限拒绝 `max_steps=9` | 成立 | 已通过实际测试验证 |
| `RunClock` 把墙钟、模型预算和工具预算分开，并用剩余墙钟裁剪后两者 | 成立 | 已通过实际测试验证 |
| 状态枚举包含计划中的终态和四种工具状态 | 成立 | 已通过实际测试验证 |
| 模型层禁止“成功同时带错误码”这类组合 | 不成立 | 已通过代码静态审查，见 P3-2 |
| `prompt_name` 已确认对应真实模板 | 尚未在 M1 做 | 已通过代码静态审查；模板存在性留给后续 PromptService |

`AgentRequest`、`AgentRunState`、`AgentResult`、`ToolExecutionResult`、`EventEnvelope` 都是 `extra="forbid"`。`ExecutionPolicy` 和 `RegisteredToolView` 额外 `frozen=True`。`RequestedPolicy` 本身不冻结，合并时会把数值和工具名复制进新的冻结策略；现有测试在合并后修改原请求，有效策略保持不变。

`EventEnvelope` 适合作为后续事件信封：有 `request_id`、`task_id`、从 1 开始的 `sequence`、时间戳和闭集 `type`。`data` 仍是任意字典，模型注释已写明载荷过滤不在本里程碑。时间戳只检查长度，不检查 UTC 格式。这留给 M5。

`UsageSnapshot` 不要求 `prompt_tokens + completion_tokens == total_tokens`。当前没有 Runtime 写入它。静态审查认为这是 M5/M6 的不变量，不构成 M1 缺陷。

## 4. M2 审查结果

`ToolExecutor.execute` 的顺序是：调用 ID、注册表、风险等级、服务端白名单、本次白名单、JSON 与 Pydantic、剩余时间，然后才创建任务。权限和参数失败都发生在 `asyncio.create_task` 之前。

| 检查项 | 结论 | 证据 |
|---|---|---|
| 只读注册表，不调用 `ToolService`，源码中没有 `eval` / `exec` | 成立 | 已通过实际测试验证 |
| 未知工具、服务端名单外、本次名单外都不调用 handler | 成立 | 已通过实际测试验证 |
| 构造后扩大传入的服务端名单集合，不能放行新工具 | 成立 | 已通过实际测试验证 |
| 非法 JSON、非对象、多余字段不调用 handler | 成立 | 已通过实际测试验证 |
| 对象内部的 `NaN` / `Infinity` / `-Infinity` 被拒绝 | 成立 | 只读探针；pytest 只覆盖了整段 `NaN` 和 `Infinity` |
| 同步 handler 走 `asyncio.to_thread`，异步 handler 直接 `await` | 成立 | 已通过实际测试验证 |
| 同步 0.2 秒 handler 不挡住同循环上的其它协程 | 成立 | 已通过实际测试验证 |
| 异常信息、堆栈和参数不进入结果与错误日志 | 成立 | 已通过实际测试验证 |
| 不重试 | 成立 | 异常测试断言 handler 只进入一次；源码没有重试循环 |
| 超长结果在短工具名、上限 180 时仍是合法 JSON | 成立 | 已通过实际测试验证 |
| 任意合法工具名和合法下限都保持合法 JSON | 不成立 | 只读探针，见 P2-1 |
| 超时后迟到成功不能覆盖结果 | 成立 | 已通过实际测试验证 |
| 取消传播 `CancelledError`，对端调用仍可成功 | 成立 | 已通过实际测试验证 |
| 重复 `tool_call_id` 被拒绝 | 不成立 | 只读探针，见 P3-3 |

执行器把服务端名单复制为 `frozenset`。本次名单也在调用开始时复制为 `frozenset`。静态审查认为，调用过程中修改原集合不会改变这次检查。

## 5. ExecutionPolicy 安全性

服务端策略始终先存在。`merge_execution_policy` 只接受小于或等于服务端值的请求字段；更大则抛出 `AGENT_POLICY_INVALID`，消息里只有字段名，没有用户正文。等于上限被接受。这是明确拒绝，不是静默放宽，也不是把两个值取最大。

已通过实际测试验证的路径：

- 省略 `policy` 时十个限制字段保持服务端值，并返回新对象。
- 请求把步数、工具次数、墙钟、模型超时、工具超时、上下文和事件队列全部调小。
- `tool_timeout_seconds` 比服务端大 0.1 被拒绝。
- 只把 `max_tool_calls` 降到 1、留下更大的 `max_tool_calls_per_step` 时，整次合并失败，不会留下“每步次数大于总次数”的策略。
- `allowed_tools` 省略时沿用服务端元组；`[]` 变成空元组；`shell`、名单外已注册工具、重复名和 `../calculator` 被拒绝。
- 合并结果按名称排序，重复合并结果相同。

工具名使用 `^[A-Za-z0-9_-]{1,64}$`。请求名单必须是服务端名单的子集。用户不能用别名、路径或大小写变体扩展名单，注册表也没有别名字段。

硬上限与 `Settings` 的对应关系，已通过代码静态审查：

| 策略字段 | 硬上限 | Settings 约束 |
|---|---|---|
| `model_timeout_seconds` | 600 | `deepseek_timeout_seconds`：`gt=0, le=600` |
| `tool_timeout_seconds` | 30 | `tool_timeout_seconds`：`gt=0, le=30` |
| `max_tool_result_chars` | 96 到 20000 | `tool_result_max_chars`：`ge=96, le=20000` |

因此 `server_policy_from_settings` 在现有配置约束下不会造出超过硬上限的策略。现有测试用 5 秒、0.2 秒和 1000 字验证了收紧后的配置可以进入策略。没有测试把 601 送进 `Settings`。

默认策略里的模型超时是 60 秒，墙钟是 30 秒。字段本身可以大于墙钟。有效模型等待由 `RunClock.model_timeout` 取两者较小值，测试在起点得到的是 30 秒，不是 60 秒。后续循环必须调用这个方法。直接把 `policy.model_timeout_seconds` 交给 Provider，会放宽到字段值，并可能叠上 Provider 自己的重试。实施计划第 7 节已经把这件事列为 M4 风险。

`default_server_policy` 使用写死的 60 秒、2 秒和 4000 字。`server_policy_from_settings` 才读取第一阶段配置。两者在默认配置下一致。若环境把工具超时收成 0.5 秒，只有后者会跟着收紧。见 P3-6。

空名单的含义是禁用全部工具，不是回退到服务端全量名单。`None` 才表示沿用服务端名单。

## 6. RunClock Deadline 正确性

`RunClock` 使用 `time.monotonic`。传入 `now` 时使用调用方给的单调时间，便于测试。`duration_ms` 另用 `time.perf_counter`，只表示这次调用花了多久，不参与 deadline。

已通过实际测试验证：

- 起点剩余墙钟 30 秒，模型有效超时是 30 秒，工具有效超时是 2 秒。
- 经过 20 秒后，模型有效超时降到 10 秒。
- 剩余 5 秒时，工具有效超时仍是 2 秒，不会被墙钟误收成 5 秒，也不会放宽到 5 秒以上。
- 超过墙钟后剩余时间为 -10，工具有效超时为负数。
- 传入早于起点的 `now` 时，`elapsed` 被计为 0。

执行器在 `budget <= 0` 时返回 `timed_out` / `TOOL_DEADLINE_EXCEEDED`，并且不创建任务。`now` 恰好等于墙钟终点，以及更晚的负剩余，都走这条路径。前者已有测试；负剩余由只读探针再次观察到，handler 调用次数为 0。

剩余时间为正时，等待时长是 `min(工具超时, 剩余墙钟)`。只读探针把剩余设为 0.5 秒、工具超时设为 2 秒，`execute` 约 0.506 秒返回，状态为 `timed_out` / `TOOL_TIMEOUT`，结果中没有 handler 的成功文本。0.006 秒是单次调度余量，没有做重复计时。现有测试用剩余 0.05 秒对同一分支做了错误码和迟到结果断言，没有断言实际耗时。

`budget <= 0` 的判断在创建任务之前。校验 JSON 的时间发生在这次判断之前；`now=None` 时，剩余时间在判断点重新读取。判断之后到 `asyncio.wait` 返回，等待参数等于该预算。返回后的截断是同步工作，可能让这次协程略晚于采样点上的 deadline。静态审查认为这是毫秒级余量，不是按工具自己的 2 秒继续等待。

尚未验证：高并发下预算采样与任务完成的极端交错。单次探针和现有超时测试都没有覆盖这种压测。

`now` 早于 `started_monotonic` 时，剩余时间按整段墙钟计算。只读探针在起点 100、`now=50` 时得到剩余 30 秒、工具预算 2 秒，并且 handler 实际执行成功。生产路径应传 `now=None`。见 P3-5。

时钟对象是冻结数据类，没有可写的剩余时间槽。两个并发调用读取同一时钟时，各自计算同一个剩余值，不会互相扣减。这与“每个调用都受同一个 deadline 约束”一致。顺序循环仍然应该一个调用结束后再看剩余时间；执行器自己不会阻止调用方并发启动。

## 7. ToolRegistry 权限边界

`ToolExecutor` 不注册工具。`ToolRegistry.register` 只接受 `risk_level == "low"` 和合法名称，重复名称直接拒绝，没有注销或覆盖 API。`get` 按精确字符串查找，没有别名。

执行时的许可要同时满足：

1. 名称能在注册表中找到。
2. 当前 `ToolSpec.risk_level` 仍是 `low`。
3. 名称在构造执行器时复制的服务端 `frozenset` 中。
4. 名称在这次调用的名单中。

请求对象没有注册表写入接口。把未注册名称放进请求名单，会在策略合并时得到 `AGENT_TOOL_NOT_ALLOWED`。即便调用方绕过合并、直接把更宽的名单传给 `execute`，服务端快照和风险等级仍会拒绝。后一种情况已由执行器测试验证。

高风险分支已通过代码静态审查存在。公开 `register` 无法放入高风险工具，所以现有测试没有覆盖这个分支。只读探针在注册后把 `risk_level` 改成 `high`，执行结果为 `rejected` / `TOOL_NOT_ALLOWED`，handler 没有再被调用。

服务端白名单是构造时的快照，后续修改原集合不能放宽它。已通过实际测试验证。

工具规格本身不是快照。`ToolSpec` 是可变数据类，执行时重新 `get`。只读探针在注册后替换 `handler`，下一次执行运行了新 handler。进程内代码可以这样做。请求不能这样做。见 P3-7。

`low_risk_tool_names` 会把注册表里每一个 `low` 工具放进默认服务端名单。这与第一阶段 `schemas_for(None)` 暴露全部低风险工具的范围一致。新注册一个低风险工具后，下一次构建策略就会默认允许它。注册表没有独立的 `enabled` 开关；`RegisteredToolView.enabled` 只是 `risk_level == "low"` 的视图。

## 8. ToolExecutor 执行安全性

名称和调用 ID 都要整段匹配 `^[A-Za-z0-9_-]{1,64}$` 与 `^[A-Za-z0-9_-]{1,128}$`。不匹配时，对外名称或 ID 改写成 `invalid`，原始值不进入结果。非法 ID 在查注册表之前返回。只读探针确认 handler 未被调用。错误码使用 `INVALID_TOOL_ARGUMENTS`，见 P3-1。

JSON 使用 `json.loads(..., parse_constant=拒绝函数)`，并且要求顶层是对象。整段 `NaN`、`Infinity` 已有测试。只读探针确认 `{"text": NaN}`、`{"text": Infinity}`、`{"text": -Infinity}` 也被拒绝。重复键由标准库保留后者，只读探针看到 `{"a":1,"a":2}` 变成 `{"a": 2}`。这不构成额外字段绕过。

参数模型用注册表里的原始 `args_model.model_validate`。当前 `CalculatorArgs` 和 `EmptyArgs` 都是 `extra="forbid"`，多余字段已有测试。执行器没有再包一层强制 `forbid`。以后若有工具模型允许额外字段，执行器会按该模型接受。静态审查认为这是注册约定，不是当前两个工具的漏洞。

同步函数用 `asyncio.to_thread`，协程函数用 `await`。`inspect.iscoroutinefunction` 识别普通 `async def`。静态审查：包装后的可调用对象可能识别失败并被送进线程；当前注册的两个工具都是普通同步函数。

失败结果的公开文案是固定中文。测试确认 `RuntimeError` 文本、`ZeroDivisionError` 类型名、参数中的标记和 `Traceback` 都不在结果里，日志只有请求 ID 和工具名。`ValueError` 映射为 `rejected` / `INVALID_TOOL_ARGUMENTS`，其它 `Exception` 映射为 `failed` / `TOOL_EXECUTION_ERROR`。这与第一阶段 `ToolService` 的分类一致。

handler 返回字符串时原样放入信封。返回其它对象时先 `json.dumps(..., allow_nan=False)`，再作为字符串放进 `result`。只读探针让 handler 返回带中文和 emoji 的字典，最终内容是合法 JSON，`result` 是一段 JSON 文本，不是嵌套对象。返回含 `NaN` 的字典时，结果为 `failed` / `TOOL_EXECUTION_ERROR`，内容里没有 `NaN`。这与第一阶段“非字符串先序列化再装信封”的形状一致，同时比第一阶段更严格地拒绝非标准数值。

成功路径只 `return` 一次。超时和取消路径不读取任务结果。源码中没有第二次调用 handler 的分支。

## 9. 超时与取消机制

两种超时用不同错误码，状态都是 `timed_out`：

| 条件 | 状态 | 错误码 | handler |
|---|---|---|---|
| 剩余时间小于或等于 0 | `timed_out` | `TOOL_DEADLINE_EXCEEDED` | 不启动 |
| 剩余时间大于 0，但等待超过预算 | `timed_out` | `TOOL_TIMEOUT` | 已启动，协程不再等它 |

外层 `CancelledError` 会先断开内部任务，再原样抛出。调用方得到的是取消，不是 `ToolExecutionResult`。内部同步线程不会因为协程取消而停止。`_detach` 先登记回调再 `cancel`，回调取走异常，避免把迟到结果变成另一次返回。

已通过实际测试验证：

- 工具超时后，工作线程稍后设置的成功标记不会出现在已经返回的结果里，结果对象保持 `timed_out`。
- 剩余墙钟小于工具超时时，错误码是 `TOOL_TIMEOUT`，迟到文本不在结果里。
- 取消慢调用时抛出 `CancelledError`，快调用仍是 `succeeded`。

尚未验证：同一次等待上，超时定时器和外层取消恰好同时到期。静态审查的两个出口都不会读取成功结果。取消先到达时，异常继续向外抛。超时先返回时，`finish` 是同步代码，紧接着的返回可能让这次协程以 `timed_out` 结束，取消要到调用方的下一次挂起点才生效。这个窗口没有探针。

`asyncio.to_thread` 不能强制终止线程。实施计划已经接受“线程自然结束，但运行不能记为成功”。当前实现满足后半句。见 P2-2。

## 10. 并发执行隔离

一次 `execute` 的结果、计时和任务都是局部变量。执行器实例上可共享的只有注册表引用和已冻结的服务端名单。两个并发调用不会写同一个 `ToolExecutionResult`。

已通过实际测试验证：两个 0.25 秒工具并发执行，总耗时小于 0.45 秒，调用 ID 和结果文本各自对应，没有串结果。

只读探针在第一个同步工具超时返回后立即执行第二个快速工具。第二个结果为 `succeeded`。第一个线程当时仍未结束；结束后，第一个结果对象仍是 `timed_out`，内容里没有迟到文本。

尚未验证：把默认线程池占满之后，后续同步工具需要排队多久。执行器没有信号量。见 P2-2。

取消路径没有共享的“当前任务”字段。A 的 `CancelledError` 只属于 A 的协程。测试里的对端是短调用，不是第二个同样很长的调用。就执行器自身的状态而言，静态审查没有发现 A 可以写到 B 的结果。

## 11. 第一阶段兼容性

`git diff 7063d99..HEAD --name-only` 只包含：

- `app/agents/` 下四个新文件
- `tests/agents/` 下两个新测试
- `phase-skills/deepseek-fastapi-phase2-skill/` 中四份路径文字

`9b609ba` 只调整 skill 文档里的项目路径。`a80a781` 是 M1，`1f1751b` 是 M2。

以下路径不在该 diff 中：`app/tools/`、`app/providers/`、`app/services/`、`app/api/`、`app/schemas/`、`app/core/`、`app/main.py`，以及原有 `tests/test_*.py`。据此确认 Provider 行为、`ToolService`、注册机制、路由、Schema、错误码和 SSE 契约没有被这两次里程碑修改。

原有测试文件没有删除。全量运行仍有 4 个跳过，都来自 `tests/test_live_deepseek.py` 的模块级条件：只有 `DEEPSEEK_LIVE=1` 才访问真实 DeepSeek。跳过的四个用例是：

- `test_live_chat_completion_and_schema`
- `test_live_sse_streams_until_done`
- `test_live_tool_loop_executes_and_answers`
- `test_live_short_timeout_is_safe`

数量与基线说明中的“57 个通过、4 个 live 跳过”相符：本次全量 104 个通过，减去新增 47 个 Agent 测试，剩下 57 个。这是同一次全量运行的数量对照，没有另开一次只含旧文件的进程。

`ToolExecutor` 复制了第一阶段的 JSON 信封和截断回退，但没有修改 `ToolService`。第一阶段执行路径仍是校验后抛异常；Agent 执行路径返回结构化结果。两套语义并存，旧接口没有改用新执行器。

## 12. 专项场景分析

### 场景 A：总 deadline 还剩 0.5 秒，工具超时为 2 秒

结论：等待被收成剩余墙钟，不会按 2 秒等待。剩余时间耗尽时不启动 handler。

| 问题 | 结论 | 证据 |
|---|---|---|
| 实际等待是否约为 0.5 秒 | 是。探针中 `execute` 耗时 0.506 秒 | 只读探针；同等 `min` 分支已有 0.05 秒测试 |
| 是否按工具自身的 2 秒继续等待 | 否 | 已通过代码静态审查，并由探针的耗时支持 |
| 剩余时间小于或等于 0 时是否拒绝启动 | 是，错误码 `TOOL_DEADLINE_EXCEEDED` | 已通过实际测试验证；负剩余另有只读探针 |
| 0.5 秒这条路径的状态 | `timed_out` / `TOOL_TIMEOUT`，因为预算仍大于 0，handler 会启动 | 只读探针 |
| 边界竞争 | 单次探针没有出现迟到成功回写。调度余量约 6 毫秒。未做并发压测 | 只读探针 + 尚未验证 |

剩余 0.5 秒时启动 handler 是这条分支的既定行为。总 deadline 限制的是协程还要等多久，不是在剩余时间较少时禁止一切工作。线程仍可能在返回后继续跑，直到函数自己结束。

### 场景 B：同步工具超时后继续执行，再提交第二个工具

结论：第一个结果保持超时；第二个调用可以成功；线程不会被杀死。

| 问题 | 结论 | 证据 |
|---|---|---|
| 第一个调用返回 `timed_out` | 是 | 已通过实际测试验证 |
| 迟到线程覆盖超时结果 | 不能。结果对象在事件循环上生成，线程没有该对象的引用 | 已通过实际测试验证；只读探针在线程结束后复查仍为超时 |
| 第二个工具能否执行 | 能。探针中的快速工具返回 `succeeded` | 只读探针 |
| 执行器是否串写共享结果 | 没有共享结果槽 | 已通过代码静态审查 |
| 线程是否继续占用资源 | 是，直到 handler 返回 | 已通过实际测试验证线程确实跑完；见 P2-2 |
| 是否需要限制未来并发 | 需要。默认线程池会被未结束的同步调用占住 | 已通过代码静态审查 |

现有超时测试的睡眠是 0.4 秒、预算 0.05 秒，不是题目中的 3 秒和 1 秒。探针使用 1.2 秒睡眠和 0.3 秒预算，观察的是同一代码路径，不是题目数字的原样复现。

### 场景 C：并发调用中一个取消，另一个完成

结论：取消不会把对端改成失败，也不会把被取消的调用改成成功。

| 问题 | 结论 | 证据 |
|---|---|---|
| A 传播 `CancelledError` | 是 | 已通过实际测试验证 |
| B 可以完成 | 是，测试中的快工具得到自己的成功文本 | 已通过实际测试验证 |
| A 的状态写到 B | 不能 | 已通过实际测试验证 + 静态审查 |
| 共享任务槽被修改 | 执行器没有这种字段 | 已通过代码静态审查 |
| 重复完成或错误返回 | 取消任务保持 cancelled；成功任务只返回一次 | 已通过实际测试验证 |

测试没有覆盖“两个都是长调用，并且在双方都已进入线程后取消其中一个”。就已有隔离方式，这个组合仍由各自的任务和局部结果负责。尚未用该精确编排再跑一次。

### 场景 D：其它边界

| 场景 | 结论 | 证据 |
|---|---|---|
| 重复调用 ID | 两次都执行，两次都成功 | 只读探针，见 P3-3 |
| 空工具白名单 | 策略合并得到空元组；执行器返回 `TOOL_NOT_ALLOWED`，不调用 handler | 合并已通过实际测试验证；执行器路径为只读探针 |
| 未授权工具 | `AGENT_TOOL_NOT_ALLOWED` 或执行期 `TOOL_NOT_ALLOWED` | 已通过实际测试验证 |
| 非法 JSON | `rejected` / `INVALID_TOOL_ARGUMENTS` | 已通过实际测试验证 |
| 超长输出 | 短名称下是合法 JSON；长名称加下限 96 时成功内容可以不是 JSON | 测试 + 只读探针，见 P2-1 |
| Unicode | 中文和 emoji 可以进入合法 JSON 字符串 | 只读探针 |
| handler 返回 `NaN` | `failed`，内容中没有 `NaN` | 只读探针 |
| 多余参数 | 当前两个参数模型和测试工具都拒绝 | 已通过实际测试验证 |
| 超时与取消同时发生 | 两条路径都不应报告成功；精确交错尚未验证 | 已通过代码静态审查 |
| 不同异常 | `RuntimeError`、`ZeroDivisionError`、`ValueError` 已分类；`CancelledError` 从 handler 抛出时会向外传播，尚未验证 | 测试 + 静态审查 |
| 深层 JSON | 深度 2000 被解析；深度 5000 在 `_parse_object` 抛出 `RecursionError` | 只读探针，见 P2-3 |

## 13. 测试执行结果

命令在项目根目录执行，解释器为 `.venv\Scripts\python.exe`。pytest 报告的运行环境是 Python 3.13.6、pytest 9.2.0。

聚焦测试：

```text
.\.venv\Scripts\python.exe -m pytest tests/agents/test_policy.py tests/agents/test_executor.py -v
```

| 项目 | 结果 |
|---|---|
| 收集并执行 | 47 |
| Passed | 47 |
| Failed | 0 |
| Skipped | 0 |
| 耗时 | 2.19 秒 |

其中策略 24 个、执行器 23 个。`max_steps` 的 0、负数、布尔和字符串等 9 个参数化用例，以及 10 个非法参数用例，都计入上述总数。

全量回归：

```text
.\.venv\Scripts\python.exe -m pytest -q
```

| 项目 | 结果 |
|---|---|
| Passed | 104 |
| Failed | 0 |
| Skipped | 4 |
| 耗时 | 2.98 秒 |

没有失败，也没有新增跳过。4 个跳过仍是默认不访问真实 DeepSeek 的 live 用例。相对基线的 57 个通过项，本次多出来的是 47 个 Agent 测试。

只读探针不是上述数字的一部分。

## 14. 测试覆盖缺口

现有测试已经覆盖策略收紧、名单拒绝、冻结后的隔离、deadline 拒绝启动、迟到成功不回写、取消与对端隔离、异常脱敏和短名称截断。

没有写入测试、本次用探针看到的行为：

- 剩余恰好 0.5 秒时的实际等待时间。
- 超时返回后立刻再跑一个工具。
- 对象内部的非标准 JSON 数值。
- Unicode 与 handler 返回 `NaN`。
- 重复调用 ID、空执行名单、非法调用 ID。
- 64 字符工具名在上限 96 时的截断。
- 深度 5000 的 JSON。
- `now` 早于时钟起点。
- 注册后修改 `risk_level` 或 `handler`。

仍未验证：

- 超时和取消在同一次 `await` 上同时到期。
- 线程池被多个已超时同步工具占满后的排队时间。
- handler 主动抛出 `CancelledError` 或 `SystemExit`。
- 以两个长调用精确复现场景 C。

这些缺口不推翻已通过的 47 个测试。P2-1 和 P2-3 说明“超长结果仍是 JSON”和“坏参数都变成结构化结果”这两句，只在现有用例的输入范围内成立。

## 15. 问题清单

### P2-1 成功截断的回退可能切出非法 JSON

- 严重等级：P2
- 文件：`app/agents/executor.py`
- 位置：`_bounded_tool_content`，约 220–243 行。第一阶段 `app/services/tool_service.py` 约 122–123 行有相同回退，M2 没有改那个文件。
- 表现：当最短截断信封都放不进上限时，函数把完整 JSON 切一段，再接上 `...[truncated]`。状态仍是 `succeeded`，`error_code` 仍是空。长度不超过上限，但文本不再是 JSON。
- 触发条件：`len(名称) + 65 > max_tool_result_chars`，并且完整结果也超过上限。上限合法下限是 96，因此名称长度至少 32 才可能进入回退。名称长 64 时，连短结果的信封也超过 96，成功输出都会走回退。
- 本次测量：`calculator` 的截断信封是 75，`get_current_time` 是 81，都小于 96。默认上限 4000 大于任何合法名称的截断信封（最长约 129）。当前两个工具在任何合法上限下都不会走这条回退。只读探针对 64 字符名称和上限 96 得到长度 96 的非法 JSON，`json.loads` 报未结束字符串。
- 影响范围：以后注册长名称低风险工具，并且请求把结果上限收到 96 附近时，模型会收到一份被标记为成功的坏 JSON。
- 修复建议：信封放不下时返回固定的合法错误 JSON，并把状态改为 `failed` 或保持失败码；不要切片拼接。当前两个工具可以另加一条长名称回归，避免只测 `echo` 和 180。
- 是否影响 M3：不阻断架构设计。M3 编码前应先修。上下文管理不能假定所有 `succeeded.content` 都能被解析。

### P2-2 超时或取消后的同步线程继续占用线程池

- 严重等级：P2
- 文件：`app/agents/executor.py`
- 位置：`_invoke` 约 170–174 行，`_detach` 约 246–250 行。
- 表现：协程按时返回超时或继续抛出取消。底层 `asyncio.to_thread` 线程继续执行，直到 handler 返回。执行器没有并发上限。
- 触发条件：同步 handler 的运行时间长于预算，或者外层任务在线程结束前被取消。
- 影响范围：少量计算器调用可以接受，实施计划已写明线程可以自然结束。大量超时会占住进程默认线程池，后续同步工具需要排队。结果隔离本身成立，不会因此把超时改成成功。
- 修复建议：M3/M4 设计里给一次 run 加同步工具并发上限。不要在执行器里假装能够杀死线程。有副作用的未来工具还需要自己可中断，或者放到可回收的进程里。
- 是否影响 M3：不要求先改执行器。架构设计必须把这个上限写进去。

### P2-3 参数解析没有大小限制，深层 JSON 的异常会逃出结构化结果

- 严重等级：P2
- 文件：`app/agents/executor.py`
- 位置：`_parse_object` 约 177–187 行；捕获列表约 93–101 行，只包含 `JSONDecodeError`、`ValidationError`、`ValueError`、`TypeError`。
- 表现：解析在事件循环上同步进行，调用前不限制字符串长度。只读探针中深度 2000 的对象被接受，深度 5000 在 `_parse_object` 抛出 `RecursionError`。该异常继承 `RuntimeError`，不在上面的捕获列表里，因此会离开 `execute`，而不是变成 `rejected`。
- 触发条件：模型或调用方传入约数万字符的深层 JSON。这次探针直接调用 `_parse_object`，没有再包一层 `execute`；由捕获列表可以确定 `execute` 接不住它。
- 影响范围：当前没有 Agent HTTP 入口。M4 若把模型输出原样传入，一次深层参数就能让这次调用以未捕获异常结束。超大但结构较浅的 JSON 则会堵住事件循环。权限检查在解析之前，所以这不是名单绕过。
- 修复建议：为 `arguments` 设置字符上限和嵌套深度；解析阶段捕获 `Exception`，统一返回 `INVALID_TOOL_ARGUMENTS`。M4 循环仍应把执行器外面的异常当成失败，不能当成工具成功。
- 是否影响 M3：设计时要定参数上限。编码前应收口这个异常。

### P3-1 非法调用 ID 被改写，错误码属于参数类

- 严重等级：P3
- 文件：`app/agents/executor.py`
- 位置：约 47–70 行。
- 表现：非法 ID 的结果使用 `tool_call_id="invalid"`，错误码是 `INVALID_TOOL_ARGUMENTS`。handler 不会运行。只读探针确认了这个结果。
- 触发条件：ID 为空、含空白或其它正则之外的字符。
- 影响范围：M4 若用结果里的 ID 去对模型的 `tool_call_id`，会对不上。安全文本没有回显原始 ID。
- 修复建议：循环在执行前拒绝非法 ID；执行器可以保留专门错误码。在修好之前，消息关联必须使用模型原始 ID，不能使用被改写后的值。
- 是否影响 M3：设计时写明关联规则即可。

### P3-2 领域模型允许不一致的状态组合

- 严重等级：P3
- 文件：`app/agents/models.py`
- 位置：`AgentRunState` 约 85–93 行，`AgentResult` 约 96–106 行，`ToolExecutionResult` 约 109–117 行，`EventEnvelope` 约 120–130 行。
- 表现：模型没有校验“完成则不应有错误码”“失败则应有错误码”“成功的工具结果不应带错误码”。`EventEnvelope.data` 也还不是过滤后的闭集。
- 触发条件：后续 Runtime 直接构造这些对象并写错字段。当前没有这种 Runtime。
- 影响范围：M5 的终态不变量如果只靠调用方记忆，可能出现两个终态或成功带错误。
- 修复建议：状态机放在 TaskManager。模型可以用校验器防止不可能的组合，也可以继续做纯数据并在 M5 集中测试。事件载荷过滤仍按计划留到 M5。
- 是否影响 M3：上下文设计不依赖这些终态。M5 前要选定落点。

### P3-3 重复调用 ID 会各执行一次

- 严重等级：P3
- 文件：`app/agents/executor.py`
- 位置：`execute` 整体；没有 ID 登记表。
- 表现：只读探针对同一个 ID 调用两次，handler 运行两次，两次都是 `succeeded`。
- 触发条件：调用方重复提交同一个 ID。
- 影响范围：计算器重复计算没有副作用。以后的写入类工具会造成重复副作用。执行器无法区分“另一次 run 的相同 ID”和“同一次响应里的重复 ID”。
- 修复建议：去重放在一次模型响应的循环里，不要放进可跨 run 复用的执行器。
- 是否影响 M3：M4 设计要在执行前检查 ID 唯一性。

### P3-4 错误内容超限时两条分支返回同一对象

- 严重等级：P3
- 文件：`app/agents/executor.py`
- 位置：`_error_content` 约 199–217 行。
- 表现：压缩后的错误 JSON 若仍超过上限，函数仍返回这段压缩 JSON。两个分支的返回值相同。
- 触发条件：以当前文案，六个错误码的压缩长度是 81 到 91，都小于下限 96。这条超限分支现在走不到。名称很长时，带名称的第一版会超过 96，然后正确落到压缩版。
- 影响范围：当前错误结果仍是合法 JSON，并且不超过下限。以后若把错误文案加长，可能超过调用方要求的上限。
- 修复建议：压缩版仍超限时返回一条更短且合法的固定 JSON，并加测试锁住 96 的下限。
- 是否影响 M3：不阻断。

### P3-5 早于起点的 `now` 会把剩余时间算满

- 严重等级：P3
- 文件：`app/agents/policy.py`
- 位置：`RunClock.elapsed` 约 112–116 行。
- 表现：`now < started_monotonic` 时耗时计为 0，剩余时间等于整段墙钟。现有测试锁定了这个钳制。只读探针因此在“已经给了更早时间戳”的情况下实际执行了工具。
- 触发条件：生产代码传入另一套时间，或传入 0 这类占位值。`now=None` 使用单调钟，不会走这里。
- 影响范围：误用时，deadline 检查按 run 刚刚开始处理，工具最多仍受 `tool_timeout_seconds` 和墙钟上限约束，不会超过硬上限。
- 修复建议：M3 生产路径不要传 `now`。若要防误用，可在 `now` 早于起点时拒绝启动，并单独保留测试注入方式。
- 是否影响 M3：设计约定必须写明。

### P3-6 两套服务端策略入口对配置的读取不同

- 严重等级：P3
- 文件：`app/agents/policy.py`
- 位置：`default_server_policy` 约 143–157 行，`server_policy_from_settings` 约 160–174 行。
- 表现：前者使用文档中的 60 秒、2 秒和 4000 字。后者使用 `Settings` 里的模型超时、工具超时和结果上限。默认配置下二者相同。收紧环境变量后，只有后者变严。
- 触发条件：Runtime 在非默认配置下调用 `default_server_policy`。
- 影响范围：Agent 工具超时可以比同一进程里的第一阶段工具超时更宽，但仍不超过 2 秒这一写死默认和 30 秒硬上限。用户请求仍然不能再放宽。
- 修复建议：M3 只把 `server_policy_from_settings` 当作进程级天花板，再对单次请求做合并。
- 是否影响 M3：设计要指定唯一入口。

### P3-7 执行时读取可变的 ToolSpec

- 严重等级：P3
- 文件：`app/tools/registry.py` 约 19–25 行；`app/agents/executor.py` 约 71–80 行。
- 表现：公开注册不能覆盖同名工具，服务端名单也不会随原集合变大。执行时却重新读取 `ToolSpec`。只读探针替换 handler 后，下一次调用执行了新函数；把风险改成非 `low` 后调用被拒绝。
- 触发条件：同进程代码持有 `ToolSpec` 并修改它。请求路径做不到。
- 影响范围：进程内误改 handler 会改变后续 Agent 调用。第一阶段 `ToolService` 同样在执行时读取注册表。
- 修复建议：保持注册表只由启动代码写入。若要更严，可在构建策略时冻结名称、风险和 handler 的快照。
- 是否影响 M3：不阻断。不要在运行中改已注册工具。

没有 P0，也没有 P1。

## 16. 修复建议

编码 M3 之前，建议只修这两处执行器行为：

1. 截断信封放不进上限时，返回合法的失败 JSON，不要切碎成功结果。
2. 解析参数时限制长度和深度，并捕获 `RecursionError` 等解析异常，全部收成 `rejected`。

架构设计里写明，暂不改代码：

1. 进程级策略使用 `server_policy_from_settings`，单次请求只经 `merge_execution_policy`。
2. 模型等待调用 `RunClock.model_timeout`，工具等待继续调用 `RunClock.tool_timeout`。生产代码使用 `now=None`。Provider 整次调用，包括重试，都要包在剩余 deadline 里。
3. 一次 run 的同步工具要有并发上限。超时线程只保证不再上报成功。
4. 工具参数设最大字符数。循环把执行器之外的异常当成 run 失败。
5. 同一模型响应里的 `tool_call_id` 在执行前去重。非法 ID 用原始值关联失败消息，或者在执行前拒绝。
6. 终态迁移、事件载荷过滤留到 M5。M3 的上下文只消费已经结构化的工具结果，并且要容忍失败内容。

不建议在进入设计前重写注册表、冻结全部领域模型，或改第一阶段 `ToolService`。

## 17. M3 准入建议

**C：有条件进入 M3 架构设计。**

允许开始 M3 的架构设计。不允许把本结论理解成可以开始写 ContextManager、AgentLoop 或 Runtime。

条件：

- 设计把 P2-2、P3-1、P3-3、P3-5、P3-6 写成 Runtime 约束。
- M3 编码前修复 P2-1 和 P2-3。
- 当前两个内置工具的权限、收紧策略和 deadline 拒绝启动，可以作为 M3 设计的既定基础。

若后续评审把“合法下限下的长工具名会返回成功的非法 JSON”定为必须先修的关键 P2，准入结论应改为 B。本次不这样定，因为默认上限和现有两个工具都走不到该回退，而且它不放宽权限、不延长 deadline、也不把超时报成成功。

## 18. Git 状态

审查开始时和全量测试之后、写入本报告之前：

```text
git status --short
```

输出为空，工作区干净。

```text
git log --oneline -6
```

```text
1f1751b feat: add agent tool executor with deadline and cancellation handling
a80a781 feat: add agent domain contracts and execution policy
9b609ba docs: align phase 2 skill paths with the project root
7063d99 release: DeepSeek FastAPI Phase 1 v0.1.0 baseline
```

`git rev-parse 7063d99` 为 `7063d99a5ff8ae71643d792d4543c872b5848ff7`。`git tag --points-at 7063d99` 为 `v0.1.0-phase1-baseline`。标签仍指向该基线。本次没有执行 reset、checkout、restore、commit、tag、push 或 clean。

`git diff 7063d99..HEAD --stat` 为 10 个文件、1428 行新增量级的变更，其中运行时代码只有 `app/agents/` 的新文件和 `tests/agents/` 的新测试。没有发现对第一阶段运行时代码的意外修改。

本报告是审查结束后唯一新增的文件。
