# M2.0 共享 Run Core 只读审计

本次没有修改业务代码、没有创建迁移、没有提交或推送。结论来自当前工作区源码和一次只读的 `information_schema.tables` 查询。列级结构以 ORM 为准；没有逐列比对 Alembic 与线上表。

## 1. 审计基线

| 仓库 | 路径 | HEAD |
|---|---|---|
| 后端 | `muzi-llm-server` | `4be86a97702cea679891394faf2a3daf1e9b6a27` |
| 前端 | `muzi-llm-frontend` | `a2812c760718e9fed5635db528aa5d6c0d02065e` |

已验收功能仍有未跟踪文件，不能按 HEAD 判断它们不存在。

后端未跟踪：`app/api/agent_access.py`、`app/authorization/application/chat_run_detail.py`、`app/schemas/chat_run.py`，以及对应测试。

前端未跟踪：`RunDetailTrigger.vue`、`RunDetailPanel.vue`、`chatRunDetail.service.js`。

已修改且属于 M1 / SEC-P0 的跟踪文件包括 `chat.py`、`me.py`、`run_registry.py`、`history_service.py`、`agents.py`、`planning.py`、`execution_guard.py`、`ChatSession.vue`、`rbac.js`。SEC-P0 的 `require_agent_operator`、`ExecutionGuard` 仍在工作区，没有被 M1 覆盖掉。

线上 `public` 中，候选表全部存在：`sessions`、`messages`、`plan_events`、`plans`、`agent_tasks`、`executions`、`events`、`tool_calls`、`usage_records`、`llm_usage_records`、`tool_usage_records`、`skill_usage_records`、`chat_history_sessions`、`chat_history_messages`。

## 2. 三模式真实调用链

### Ask

1. 页面是 `ChatView` 下的 `ChatSession.vue`。发送进入 `handleUserMessage` → `postSend` → `startNewConversation`。
2. 模式来自 `userData.chatMode`，经 `normalizeChatMode`（`chatModes.js:11`）。`AGENT_MODE_ENABLED` 为 `false`（`chatModes.js:2`），界面不能选 Agent。
3. 非 Plan、非方舟、已登录时，`ChatSession.vue:1989-1993` 生成 `crypto.randomUUID()` 作为 `clientRunId`，写到当前助手消息的 `chatRunId`。
4. `sseFetch` → `streamServerChat`，`POST /api/v1/chat/completions/stream`。请求体字段在 `StreamChatRequest`（`schemas/chat.py:200-205`）：`mode`、`conversation_id`、`client_run_id`。
5. `stream_completion`（`chat.py:410`）。`mode == "agent"` 时先 `require_agent_operator`。有 `conversation_id` 时校验登录、`chat.access` 或 `agent.execute`、会话归属和额度授权。
6. 有会话时 `run_id = body.client_run_id or prepared.request_id`（`chat.py:512`），注册到进程内 `ChatRunRegistry`。
7. `ChatService.iter_sse`（`chat_service.py:397`）有工具时走 `_iter_ask_sse`，否则 `_iter_plain_sse`。Provider 只有启动时构造的 `DeepSeekProvider`。
8. 工具在 `ToolService.execute`，执行前过 `authorize_registered_tool`。一轮用户消息可以多次调用模型。
9. SSE 由 `format_sse` 产生。前端 `serverChat.service.js` 把 `delta`、`usage`、`done`、`tool` 交给 `updateAIMessages`、`applyRoundUsage`、`showAskTool`。
10. 有会话时 `_persisted_stream` 先写 user `messages` 和 `chat.run.started`。`seal_terminal`（`chat.py:110`）在把 `done` 或 `error` 发给客户端之前提交终态和 assistant 消息。没有这两类事件时，循环正常结束后会提交默认的 `chat.cancelled`。
11. 结算键是 `{run_id}:{usage_round}`，差额键是 `{run_id}:rest`（`chat.py:180`、`226`）。`QuotaService.settle` 按用户、来源、结算键去重。
12. 详情读取 `GET /api/v1/me/chat-runs/{run_id}`（`me.py:247`）。`resolve_chat_run_status`（`chat_run_detail.py:81`）只在注册表确认活动任务时返回 `running`，否则缺终态为 `unknown`。
13. 刷新靠 `serializeMessage` 保留 `chatRunId`（`ChatSession.vue:1120`）和 Pinia。云端历史把该字段放进 `chat_history_messages.planning`。

`client_run_id` 只有长度校验（8–128）。没有服务端唯一约束，也没有占用检查。

### Plan

1. `chatMode === 'plan'` 时 `startNewConversation` 在 `ChatSession.vue:1929` 转入 `runPlanningMessage`（`2038`），不走 Ask 的 `sseFetch`，也不写 `chatRunId`。
2. 前端先 `POST /api/v1/conversations`，再 `POST /api/v1/plans`，随后 `POST /api/v1/plans/{plan_id}/messages/stream`。
3. `plan_message_stream`（`planning.py:186`）要求登录用户，并先 `QuotaService.authorize`。
4. 计划身份是 `plans.public_id`。版本是 `plans.current_revision` 与 `plan_revisions.revision`，唯一约束是 `(plan_id, revision)`。
5. 流由 `SqlPlanningStore.process_message_stream` 产生 `delta`、`plan`、`interaction`、`done`。事件写入 `plan_events`，序号在会话内唯一。
6. 等待用户时交互行在 `plan_interactions`，状态包含 `pending`。提交幂等键是 `(plan_id, client_submission_id)`。
7. 结算键是 `{request_id}:{plan_id}:{revision}`（`sql_store.py:319`），来源 `planning`。这不是 Ask 的 `client_run_id`。
8. 交接执行要单独批准、切换和授权（`sql_store.py:338-339`）。授权后 `planning.py:347` 写入 `AgentTask` 和 `TaskExecution`，再调用 `AgentRuntime.start`。
9. 前端把 Agent 交接结果放在消息的 `message.run`，不是 `chatRunId`。`AGENT_MODE_ENABLED` 为 false 时，页面不能从计划切到 Agent。

### Agent

1. 对话页当前不能选择 Agent。HTTP 入口仍是 `POST /api/v1/agents/runs`，`create_run`（`agents.py:56`）。聊天流 `mode=agent` 也会在进模型前要求 `agent.execute`。
2. `require_agent_operator` 先登录再查 `agent.execute`。数据库不可用时拒绝，不降级成匿名。
3. 无 `conversation_id` 时，`TaskManager.create`（`task_manager.py:64`）用 `secrets.token_urlsafe(16)` 生成内存 `task_id`，不写 `agent_tasks`。
4. 有 `conversation_id` 时，`task_id` 是新 UUID 的 hex（`agents.py:83-84`），另有 `execution_id`。同时插入 `agent_tasks`（`created`）和 `executions`（`pending`）。
5. `AgentRuntime.start`（`runtime.py:89`）把 `owner_id` 放进内存任务。工具名由服务端权限和客户端 grants 的交集决定。执行循环仍在 runtime 内。
6. 有持久化观察器时，`AgentRunPersistence._write_serial`（`persistence.py:49`）把过程写入 `events`。`agent.started` 把任务和 execution 改成 `running`。完成、失败、取消、达到上限时写终态。
7. 额度在每个 `agent.model.completed` 结算，键是 `{task_key}:{step}`（`quota_bridge.py:119`），来源 `agent`。
8. 读取、取消、订阅要核对内存 `owner_id` 或 `agent_tasks.user_id`。不匹配返回 404。

三套标识不是同一层：

- Ask：`client_run_id` 或 HTTP `request_id`
- Plan：`plans.public_id` + revision；结算再加本次 `request_id`
- Agent：内存或持久化 `task_id`，其下 `execution_id`，HTTP `request_id` 只是请求号

一个 `sessions.id` 可以挂多次 Ask、一个或多个 Plan、多个 Agent 任务。没有一个现成 ID 能同时取出这三类的完整运行。

## 3. 数据表关系

ORM 定义在 `tables.py`、`planning.py`、`metering.py`、`rbac.py`。Alembic 最早的核心表在 `6de1140597d2_create_core_tables.py`，规划表在 `f38a1d2e9c70_interactive_planning_persistence.py`，计量表在 `d5a2c8e14b70_usage_metering.py`。下面的约束来自 ORM。线上只确认了表存在。

| 表 | 主键 | 归属 | 运行标识 | 状态 | 幂等 |
|---|---|---|---|---|---|
| `sessions` | UUID | `user_id` → `users.id` | 无 run 列 | `active/archived/closed` | 无运行唯一键 |
| `messages` | UUID | 经 `session_id` | 无 | 无 | 无 |
| `plan_events` | UUID，另有唯一 `event_id` | 经 `conversation_id` → `sessions.id` | `payload.run_id` 仅 JSON | 事件类型本身 | `(conversation_id, sequence)` 唯一 |
| `plans` | UUID，公开号 `public_id` 唯一 | `owner_id` | `public_id` | 规划状态机 | 无 run 键 |
| `agent_tasks` | UUID | `user_id`，可空 `session_id` | `request_id` 可空；内存任务不一定入库 | `created` 起 | `request_id` 只是索引 |
| `executions` | UUID | 经 `task_id` | 无独立公开号 | `pending` 起 | 无 |
| `events` | UUID | 经 `task_id` | `sequence` | `event_type` | `(task_id, sequence)` 唯一 |
| `tool_calls` | UUID | 经 `execution_id` | `tool_call_id` 可空 | `running` 起 | 无唯一 `tool_call_id` |
| `usage_records` | UUID | `user_id` | `settlement_key` | `settled/exceeded/unsettled` | `(user_id, source, settlement_key)` 唯一 |
| `llm_usage_records` | UUID | `user_id` | `settlement_key`，可空 `task_id` | completed 等 | `(user_id, settlement_key)` 唯一 |
| `tool_usage_records` | UUID | `user_id` | `task_id` 可空 | 字符串 | `(user_id, tool_call_id)` 唯一 |
| `skill_usage_records` | UUID | `user_id` | `skill_execution_id` | 字符串 | `(user_id, skill_execution_id)` 唯一 |

`tool_calls` 在 `app` 内没有找到插入语句。工具用量实际写在 `tool_usage_records`。`events` 只由 `AgentRunPersistence` 和 `agents.py` 写入，Ask 不写这张表。

Ask 的运行号只存在 `plan_events.payload` 和前端消息里。`messages` 不保存 `run_id`。`chat_history_sessions.server_conversation_id` 没有外键。

## 4. 状态机对照

公共候选状态没有被三种模式共用。

Ask，以 `plan_events` 和 `resolve_chat_run_status` 为准：

```text
chat.run.started
  ├─ 本进程注册表仍有该任务 → running
  ├─ chat.done → completed
  ├─ chat.error → failed
  ├─ chat.cancelled → cancelled
  └─ 无终态且注册表没有任务 → unknown
```

多条终态时取序号最大的一条。服务重启后注册表清空，未写入终态的轮次变成 unknown。正常断开且没有 `done`/`error` 时，现有 `seal_terminal` 会写 `chat.cancelled`，那是真实事件，不是读取时推断。

Plan：

```text
understanding → clarifying → drafting → awaiting_approval
  → approved → ready_for_handoff → executing
  → completed / paused / cancelled / failed / superseded
```

交互另有 `pending` 到 `submitted/completed/skipped/cancelled`。这里的 waiting 是计划交互，不是 Ask 的 running。

Agent 持久化任务：

```text
agent_tasks.created + executions.pending
  → agent.started：两者 running
  → completed / failed / cancelled / limit_reached
```

无会话的 Agent 只有内存 `TaskManager`，重启即丢失。没有 `unknown` 状态。

多 Worker：`ChatRunRegistry` 和 `TaskManager` 都是进程内字典。第二个 worker 看不到第一个 worker 的 running 或取消目标。

终态重复：Ask 的 `seal_terminal` 用内存标志避免同一次流写两次。读取时迟到的另一条终态会按序号覆盖展示，不改旧行。Agent 事件靠 `(task_id, sequence)` 唯一。Plan 事件靠会话序号唯一。

取消竞态：Ask 取消只取消本进程任务。Agent 取消先核对 owner。Plan 取消走计划动作，不走 `ChatRunRegistry`。

断线：Ask 前端 `abortConnection` 会中止 fetch。终态若已提交则保持；若进程在提交前退出，重启后是 unknown。Plan 和 Agent 的 SSE 没有复用这套详情查询。

## 5. 安全风险

- Ask 详情按 `sessions.user_id` 过滤，相同 `client_run_id` 字符串不能读到别人的事件。未登录 401，不存在 404。
- `client_run_id` 不校验全局唯一。同一用户重复使用时，读取会选最晚的 `chat.run.started` 所在会话。
- Agent 创建已要求 `agent.execute`，内存任务带服务端 `owner_id`。这是未提交的 SEC-P0，不在 HEAD 里。
- 工具执行还有 `ExecutionGuard`。敏感写入仍要求已验证的工作区授权；该绑定目前没有数据，所以高风险工具保持拒绝。工作区读取归属仍未解决。
- `tool_calls.arguments` 即使将来写入也有脱敏监听。Ask 详情不返回工具参数。
- 无会话 Agent 不落库，其他进程无法用数据库核对 owner。API 仍查内存 owner。

## 6. Usage 与 Quota

Token、额度和工具费用是三套记录。

- Token：`llm_usage_records`，来自 Provider。
- 额度：`usage_records.amount`，`QuotaService.settle` 去重后扣减。
- 工具外部费用：`tool_usage_records.external_cost`。
- Skill 指令 Token 记在 `skill_usage_records`，注释写明不另扣额度。

结算键：

- Ask：`{run_id}:{round}` 与 `{run_id}:rest`
- Plan：`{request_id}:{plan_id}:{revision}`
- Agent：`{task_key}:{step}`

失败或取消不会因为查询详情再次结算。Ask 在每轮 `usage` 事件时结算，不依赖最终是否 `completed`。重复键返回 duplicate，不累加。

## 7. 前端复用判断

可以复用现有对话页，不能把 Plan、Agent 直接塞进现在的 Ask 详情。

- `RunDetailTrigger` 只在助手消息有 `chatRunId` 且已登录时出现。
- `RunDetailPanel` 已能显示 completed、failed、cancelled、running、unknown。
- Plan 的进度在同一条消息的计划卡片和 `message.run`。
- Agent 模式开关关闭，页面没有 Agent 任务详情入口。
- 历史序列化已保留 Ask 的 `chatRunId`。Plan 的 `plan`、`run`、交互字段本来就在 `serializeMessage` 里。

下一阶段应给 Plan、Agent 增加各自的稳定引用，再决定是否复用同一个面板。不要新做聊天页。

## 8. A/B/C 比较

| 方面 | A 适配查询 | B 最小运行元数据 | C 统一所有表 |
|---|---|---|---|
| 侵入性 | 低 | 中，只在现有开始和终态处写一行 | 高，三条执行链都要改 |
| 一致性 | Ask 依赖 JSON；Agent 内存任务查不到 | 三种表面各有一行，事件仍在原表 | 强，但要迁移历史 |
| 多进程 | 不能解决注册表 | 活动状态仍要另做租约，元数据可共享 | 同样要租约 |
| 历史 | 现有 Ask 详情可继续 | 旧行没有元数据时仍走适配 | 需要回填 |
| 性能 | JSON 过滤 `plan_events` | 按主键或 `(owner, external_id)` 点查 | 大表合并后的索引成本高 |
| 安全 | 已有归属过滤 | 元数据必须带 `owner_id` | 迁移时最容易串用户 |
| 迁移风险 | 无 | 一张可空关联表 | 高 |
| 成本 | 低，但统一不了身份 | 适中 | 高 |

推荐 B，而不是现在就建完整 Run Core。

依据：Ask 的运行号不是列；Plan 和 Agent 的身份、终态和结算键彼此不同；`executions` 只覆盖有会话的 Agent；`tool_calls` 没有写入方。适配层可以读 Ask，但不能作为三种模式的共同身份。把三套事件并成一张表会改动已验收的 Ask 详情、Plan 序号和 Agent `task_id`。

B 的最小行只保存：`run_id`、`surface`（ask/plan/agent）、`owner_id`、`conversation_id`、外部标识、终态、结算键前缀。不调用 Provider，不结算，不替代 `plan_events`、`agent_tasks` 或 `usage_records`。

## 9. M2.1–M2.5

这些阶段都还没有实现。

### M2.1 身份对照，不建表

目标：用现有 ID 写出 Ask、Plan、Agent 的只读对照，确认哪些旧记录没有外部标识。

文件：只新增测试和一份对照查询，不改运行循环。

数据库：不改。

验收：同一会话的两轮 Ask、一个 Plan、一个有会话 Agent 能分别指出自己的 ID，且互不覆盖。

### M2.2 最小元数据写入

目标：在 Ask 注册、Plan 创建、Agent `create_run` 三个已有位置各写一行。终态只更新这一行。

文件：`chat.py` 的 `seal_terminal` 附近、`planning.py` 创建计划处、`agents.py` 的 `create_run`、新的 ORM 和 Alembic。

API：不改现有 SSE。

回滚：停止写入新表，读取仍走 M1 的 `plan_events`。

验收：新 Ask 同时有元数据行和原来的 `chat.done`。旧 Ask 不回填。

### M2.3 多进程活动状态

目标：取消和 running 不再只依赖本进程字典。

这不是元数据表能单独完成的。需要明确租约或现有 Redis 的使用范围。未核实 Redis 是否已保存任务。

验收：两个进程中，另一个进程的取消对活动 Ask 生效；重启后未完成任务不是永久 running。

### M2.4 前端引用

目标：Plan、Agent 在现有消息上保存自己的外部 ID，并复用 `RunDetailPanel` 的状态文案。

文件：`ChatSession.vue`、`serializeMessage`、`history_service.py` 的字段白名单、详情 API。

验收：刷新后三种新消息都能打开详情；没有 ID 的旧消息不显示入口。

### M2.5 回归

目标：确认结算键、Ask SSE、Plan 序号、Agent owner 检查不变。

测试：现有 SEC-P0、M1 详情测试，加跨用户和重复终态。

真实联调：各发一轮 Ask、Plan，并在后端直接调用一条有会话的 Agent。额度分析页仍只读。

## 10. 未核实事项

- 没有逐列比较 Alembic 与线上列。只确认表名存在。
- 没有在多 worker 进程上实际取消一次 Ask。进程内字典的限制来自代码。
- Redis 是否已经存运行状态：未查到 `ChatRunRegistry` 或 `TaskManager` 使用 Redis。
- `tool_calls` 是否曾由已删除代码写入：当前 `app` 没有插入点。
- 无会话 Agent 的内存任务在重启后的 API 行为已由 SEC-P0 测试覆盖，这次没有再跑一遍。
- Plan 页面详情还没有和 `RunDetailPanel` 接通过。

在确认采用 B，并接受 M2.1 先不建表之前，不开始实现。
