# M2.1 Ask / Plan / Agent 运行身份对照

本阶段只增加只读对照，没有建表，没有改执行循环、SSE、结算键或权限。对照结果不是已经落库的 Run Schema。

状态：**READY_FOR_UAT**。没有标记 ACCEPTED。

## 1. Git 基线

| 仓库 | HEAD | 本阶段新增 |
|---|---|---|
| `muzi-llm-server` | `4be86a97702cea679891394faf2a3daf1e9b6a27` | `app/runtime/run_identity.py`、`tests/test_run_identity.py` |
| `muzi-llm-frontend` | `a2812c760718e9fed5635db528aa5d6c0d02065e` | 无 |

SEC-P0 和 M1 的未提交文件仍在工作区，本阶段没有改它们。前端 `ChatSession.vue` 没有重构。没有 commit，也没有 push。

## 2. Ask 身份映射

一次已落库的 Ask 由下面几层组成：

| 字段 | 实际位置 | 稳定性 |
|---|---|---|
| `sessions.id` | 会话主键 | 同一会话的多轮共用 |
| `client_run_id` 或 `prepared.request_id` | 写入 `plan_events.payload.run_id` | 两者落库后无法区分 |
| `usage_records.settlement_key` | `{run_id}:{轮次}` 或 `{run_id}:rest`，来源 `chat` | 依附运行号，不是另一个运行 |

选择发生在 `chat.py` 的 `run_id = body.client_run_id or prepared.request_id`。事件只保存选中的那个字符串。对照类型因此叫 `chat_run_id`，不叫 `client_run_id`。`request_id` 在 Ask 对照里保持空。

同一用户、同一会话、不同轮次：`conversation_id` 相同，`external_id` 不同。线上会话 `9031058b-b2c5-4280-a34c-b2cb9bc3f77e` 的两轮分别是：

- `58040e9d-ee17-47f5-a4eb-17b8f451cb4b`，结算键 `…:1`
- `3cc84685-f90e-4f4e-8a9f-f9f8f260979c`，结算键 `…:1`

两次都是 `resolved`。身份解析成功不表示运行状态是 completed。缺终态的历史轮次仍然可以有身份，状态继续由 M1 的详情读取判断。

重复提交同一个 `client_run_id`：

- 服务端仍然没有唯一约束，请求兼容行为没有改。
- 新的对照在同一个用户下看到多于一次 `chat.run.started` 时返回 `ambiguous`，`conversation_id` 为空，`candidates` 列出各次开始事件。
- 不挑选最晚的一条。
- 现有 `GET /api/v1/me/chat-runs/{run_id}` 仍会按 M1 规则选择最晚的开始事件。两个读取器在重复 ID 上会不一致。这是已知差距，留到 M2.2 决定，本阶段不改详情接口。

线上目前没有同一个 `run_id` 出现在多个会话的记录。`chat` 事件里也没有空的 `run_id`。缺标识的规则由测试覆盖，没有在现有库里碰到。

## 3. Plan 身份映射

三层不能合并：

| 层 | 外部标识 | 说明 |
|---|---|---|
| Plan | `plans.public_id`，类型 `plan_public_id` | 生命周期。`plans.id` 只放在 `internal_id` |
| Revision | 同一个 `public_id`，类型 `plan_revision`，外加 `revision` | 一张快照，不是整个 Plan |
| 模型调用 | 结算键 `{request_id}:{public_id}:{revision}`，类型 `planning_settlement_key` | `request_id` 没有单独的列 |

`plans.current_revision` 只是当前指针。线上 `plan_69c623cc65904e26bca52d70ba085845` 的指针是 3，修订历史是 0、1、2、3，内部 UUID 是 `e3c9b593-ab39-48a0-91b5-e8dd3ae0cef4`。会话是 `47be13a8-ab85-4ece-b5f3-7da32da830a7`。交互卡片 2 张，类型是 `plan_interaction`，父标识才是 Plan 的公开号。

当前指针若不在修订历史里，Plan 层返回 `ambiguous`，原因 `current_revision_not_in_history`，不会改写成另一条修订。

线上 `usage_records` 没有 `source = planning` 的行。这个 Plan 的模型调用层是 **NOT_VERIFIED**。解析规则由测试用真实键格式覆盖，没有伪造结算行。

## 4. Agent 身份映射

| 层 | 标识 | 不能代替 |
|---|---|---|
| 任务 | `agent_tasks.id` 的 hex，类型 `agent_task_id` | 不是 `executions.id` |
| 执行 | `executions.id` 的字符串，类型 `execution_id` | 用它查任务会得到 `execution_id_is_not_task_id` |
| 请求 | `agent_tasks.request_id` | 不是任务号 |
| 事件 | `events.task_id` 指向任务 UUID | 不是公开任务号 |
| 结算 | `{task_key}:{step}`，来源 `agent` | 一步一条，不是任务号本身 |

有会话时，`create_run` 同时插入任务和一条 execution。正好一条 execution 时，任务对照是 `resolved`，`execution_id` 单独填写，且不等于 `external_id`。多于一条时返回 `ambiguous`，原因 `multiple_executions`，不挑选一条。

无会话时，`TaskManager.create` 用 `token_urlsafe(16)` 生成内存任务号。对照可以解析，但 `durable` 为 false，原因 `memory_only`，没有 `conversation_id`，也没有 `execution_id`。内存记录没有 `owner_id`，或其他用户来查，都是 `unresolved`。重启后数据库里找不到它。

线上 `agent_tasks` 为 0，`executions` 为 0。有会话 Agent 的真实数据是 **NOT_VERIFIED**。没有插入测试任务。

## 5. 统一候选契约

对照对象是 `RunIdentity`。它比最初的候选 JSON 多几个字段，因为真实数据里这些身份确实分开：

```json
{
  "surface": "ask",
  "owner_id": "authenticated-user-id",
  "conversation_id": "conversation-uuid",
  "external_id": "existing-runtime-id",
  "external_id_type": "chat_run_id",
  "parent_external_id": null,
  "revision": null,
  "execution_id": null,
  "request_id": null,
  "settlement_keys": [],
  "identity_status": "resolved",
  "reason": null,
  "internal_id": null,
  "durable": true,
  "candidates": []
}
```

新增字段的原因：

- `request_id`：Agent 和 Plan 模型调用有，Ask 落库后没有独立值。
- `settlement_keys`：只回显已经存在的键，不生成新键。
- `internal_id`：`plans.id` 与 `public_id` 不同，Agent 的 UUID 与 hex 也不同。
- `durable`：区分内存 Agent。
- `candidates`：歧义时列出候选，避免只留一个空结果。
- `reason`：`ambiguous` 和 `unresolved` 的具体原因。

`identity_status` 只有 `resolved`、`unresolved`、`ambiguous`。跨模式撞号时 `surface` 为 `unscoped`，`external_id` 为空，避免被当成其中一种模式。

## 6. 身份歧义处理

| 情况 | 结果 | 是否挑选 |
|---|---|---|
| 同一用户的同一 Ask 运行号有多次开始 | `ambiguous` / `duplicate_external_id` | 否 |
| Ask 事件没有 `run_id` | `unresolved` / `missing_run_id` | 否 |
| 只有终态、没有开始事件 | `unresolved` / `start_event_missing` | 否 |
| Plan 当前指针没有对应快照 | `ambiguous` / `current_revision_not_in_history` | 否 |
| 一个任务多条 execution | `ambiguous` / `multiple_executions` | 否 |
| 用 execution_id 查询任务 | `unresolved` / `execution_id_is_not_task_id` | 否 |
| 同一字符串在两种模式都解析成功 | `ambiguous` / `cross_surface` | 否 |
| 不属于当前用户 | `unresolved` / `not_found` | 不暴露对方记录 |

本阶段只报告冲突。聊天请求仍接受重复的 `client_run_id`。

## 7. 安全边界

用户身份来自调用方传入的已认证 `owner_id`。本阶段没有新的公开查询 API。`GET /api/v1/run-identity` 返回 404。

SQL 条件：

- Ask：`sessions.user_id`
- Plan：`plans.owner_id` 并且 `sessions.user_id`
- 用量：`usage_records.user_id` 加来源
- Agent 任务：`agent_tasks.user_id`
- execution：先连到 `agent_tasks.user_id`
- 内存 Agent：比对 `TaskRecord.owner_id`，不匹配与不存在都是 `not_found`

其他用户的事件、用量和任务不会进入候选列表，因此也不会制造虚假歧义。

## 8. 真实 SQL 验证

只读查询当前 PostgreSQL。没有插入、更新或删除。

已核对：

- 同一会话两轮 Ask，运行号不同，结算键分别挂在各自的运行号上。
- 一个真实 Plan，四个修订号和两张交互卡。`public_id` 与 `plans.id` 不同。
- 线上没有重复的 Ask 运行号。

未在真实库中验证：

- 有会话的 Agent。`agent_tasks` 和 `executions` 都是 0。标记 **NOT_VERIFIED**。
- Plan 的模型调用结算键。`usage_records` 没有 `planning` 来源。标记 **NOT_VERIFIED**。
- Agent 结算键。没有 `agent` 来源。标记 **NOT_VERIFIED**。
- 线上重复 `client_run_id`。规则已由测试覆盖，库里没有样本。

## 9. 测试结果

`muzi-llm-server` 中执行：

```text
pytest tests/test_run_identity.py -q
17 passed
```

覆盖了同一会话两轮 Ask、运行号不同、重复运行号、多修订、多次 Plan 模型调用、任务与 execution 分离、有会话归属、内存 Agent、跨用户、跨模式不合并、结算键原文未改、缺标识。结算测试只读取现有源码中的键格式，没有调用 `QuotaService.settle`。

数据库关系不只依赖内存对象：查询语句编译结果包含归属列；第 8 节是对当前库的只读核对。缺数据的 Agent 和 Plan 结算没有用假行补上。

## 10. M2.2 最小字段建议

仍不创建这张表。建议下一阶段只保存指针：

| 字段 | 约束 |
|---|---|
| `id` | 主键 |
| `surface` | `ask`、`plan` 或 `agent` |
| `owner_id` | 非空，来自认证用户 |
| `conversation_id` | 可空。内存 Agent 没有会话 |
| `external_id` | 非空 |
| `external_id_type` | 非空，使用本文件第 5 节的类型 |
| `parent_external_id` | 可空 |
| `revision` | 可空 |
| `execution_id` | 可空 |
| `request_id` | 可空 |
| `durable` | 内存任务为 false |

不要给 `external_id` 加全局唯一约束。重复的 Ask 运行号必须仍能被看成 `ambiguous`。查找索引可以是 `(owner_id, surface, external_id_type, external_id)`。

不要在这张表里放 Token、额度或结算金额。结算继续留在 `usage_records`。也不要在第一张迁移里用新状态列取代 `plan_events` 或 `agent_tasks.status`。

## 11. 尚未解决

- 有会话 Agent 和 Plan 模型调用还没有真实库样本。
- Ask 的 `client_run_id` 与回退 `request_id` 落库后不可区分。
- 重复 Ask 运行号时，新对照报歧义，旧详情接口仍选最晚一条。
- 内存 Agent 不能从 PostgreSQL 恢复。
- 多进程注册表不在本阶段范围内。
- M2.2 还没有开始。
