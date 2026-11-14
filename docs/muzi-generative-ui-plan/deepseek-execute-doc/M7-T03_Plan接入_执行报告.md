# M7-T03 · Plan 接入 — 执行报告

状态：**DONE**（全链路状态机 + 交接投影 + 门禁测试通过；PostgreSQL 数据库集成与浏览器端 UAT 均已实测通过）

## 1. 工作包目标（摘自 M7 文档）

> 交互式需求收集、方案 revision、审批与交接；复用已有 Plan 持久化，不创建平行真相源。

## 2. 现状审计结论

M7-T03 的主体能力在既有 `app/planning/` 子系统中**已经落地**，本轮工作为「核对 + 补缺口 + 补测试 + 记录审查」。

| 子能力 | 实现位置 | 状态 |
|---|---|---|
| 交互式需求收集 | `PlanningDecisionEngine` + `InteractionType` 白名单 + `apply_proposal(ASK_USER)` | 已实现 |
| 方案 revision | `plan_revisions`（不可变快照）+ `revisions.py` 修订纯函数 + `compute_plan_diff` + `rollback_snapshot` | 已实现 |
| 审批 | `approve`（`awaiting_approval → approved`） | 已实现 |
| 交接 | `switch_mode`（`approved → ready_for_handoff`）+ `authorize_execution`（`ready_for_handoff → executing`） | 已实现 |
| 复用持久化 | `SqlPlanningStore` 直接读写 `plans`/`plan_revisions`/`plan_decisions`/`plan_interactions` | 已实现 |

## 3. 发现的缺口与本轮代码变更

### 3.1 缺口

`app/planning/contracts.py` 早已定义了 `PlanningEventType.HANDOFF_CREATED`（`handoff.created`）事件类型，以及 `PlanHandoff` / `HandoffStep` 交接快照契约，但 **`switch_mode` 从未构造交接快照、也从未 emit `handoff.created` 事件**。

这导致 M7 验收标准「切换模式后交互和事件链路可追溯」不完整——模式切换只有 `conversation.mode_changed` 事件，缺一份「交接了什么」的结构化、可审计记录。

### 3.2 变更（纯加法，未改状态机、未改权限语义、未破坏 ask/plan）

1. `app/planning/revisions.py`
   - 新增 `build_handoff(plan)`：从已审批 `PlanSnapshot` 派生 `PlanHandoff`（goal / confirmed_decisions / constraints / steps / authorization=`revalidate_at_execution`）。
   - 设计要点：交接快照是计划权威状态的**投影**，不写新表、不创建第二份真相源；`authorization` 固定为 `revalidate_at_execution`，明确「交接并不授予执行权限，Agent 执行前仍需重新校验」。

2. `app/planning/runtime.py::switch_mode`（内存版）
   - 在 `MODE_CHANGED` 之前追加 emit `HANDOFF_CREATED`，payload 携带 `handoff` 快照、`mode`、`revision`。

3. `app/planning/sql_store.py::switch_mode`（持久化版）
   - 同样在 `MODE_CHANGED` 之前 emit `HANDOFF_CREATED`，与内存版语义一致，事件落入同一 `plan_events` 表。

## 4. 测试用例（新增 `tests/planning/test_plan_handoff.py`，10 例）

| 用例 | 覆盖点 |
|---|---|
| `test_handoff_snapshot_is_a_projection_of_the_approved_plan` | 交接快照 = 计划投影，字段一致，authorization 正确 |
| `test_handoff_carries_confirmed_decisions_from_plan` | 需求收集的用户决策进入交接快照 |
| `test_build_handoff_rejects_plan_without_steps` | 空步骤异常路径明确（契约拒绝） |
| `test_full_plan_journey_emits_handoff_and_mode_events` | 全链路事件可追溯 + 顺序（handoff → mode → run） |
| `test_handoff_event_payload_is_structured_and_versioned` | handoff.created 事件 payload 结构化且带协议版本 |
| `test_switch_mode_rejects_unapproved_plan` | 未审批不得交接 |
| `test_switch_mode_rejects_non_agent_target` | 交接只能切 agent |
| `test_authorize_execution_rejects_before_handoff` | 未交接不得授权执行 |
| `test_authorize_execution_rejects_wrong_approved_revision` | 引用不存在的已审批版本被拒 |
| `test_cross_owner_handoff_is_rejected` | 跨用户越权被拒 |

## 5. 测试证据

```text
.\.venv\Scripts\python.exe -m pytest tests/planning/test_plan_handoff.py -q
# 10 passed in 0.03s

.\.venv\Scripts\python.exe -m pytest tests/planning/ -q
# 64 passed, 4 skipped in 0.10s
# （4 skipped = 数据库集成测试，需 RUN_PLANNING_DB_TESTS=1）
```

## 6. 验收标准逐条核查（M7-T03）

| 验收项 | 结果 | 证据 |
|---|---|---|
| 交互式需求收集 | 满足 | `apply_proposal(ASK_USER)` + `test_handoff_carries_confirmed_decisions_from_plan` |
| 方案 revision | 满足 | `revisions.py` + `test_revisions.py`（既有 54 例之一） |
| 审批与交接 | 满足 | `approve` / `switch_mode` / `authorize_execution` + 新增 10 例 |
| 复用已有 Plan 持久化，不创建平行真相源 | 满足 | `build_handoff` 仅读 `PlanSnapshot` 字段，不写新表；事件落入既有 `plan_events` |

## 7. 后续门禁 — 已闭环

本轮披露的后续门禁已全部实测闭环：

### 7.1 数据库集成：`handoff.created` 落库（RUN_PLANNING_DB_TESTS=1 + PostgreSQL）

新增数据库集成测试 `tests/planning/test_sql_store_integration.py::test_handoff_created_event_is_persisted_and_replayable`，验证 `handoff.created` 事件落库到 `plan_events`、payload 结构化、且可通过 `events_after` / `conversation_events_after` 重放。

```text
$env:RUN_PLANNING_DB_TESTS="1"
.\.venv\Scripts\python.exe -m pytest tests/planning/test_sql_store_integration.py -q
# 5 passed in 0.88s
```

真实 UAT 流程产生的 `plan_events` 事件流（PostgreSQL `muzi_llm` 实测）：

| sequence | event_type | 关键 payload |
|---|---|---|
| 1 | plan.created | — |
| 2 | interaction.required | — |
| 3 | interaction.submitted | — |
| 4 | plan.updated | — |
| 5 | interaction.required | — |
| 6 | plan.approved | — |
| **7** | **handoff.created** | mode=agent, approved_revision=5, authorization=`revalidate_at_execution`, steps=7 |
| 8 | conversation.mode_changed | mode=agent |
| 9 | run.started | authorization_id 已生成 |
| 10 | run.completed | — |

同时确认 `handoff.created` 事件也写入 `plan_event_outbox`（`event_id=evt_08363c8e...`），满足事务性持久化 + 可靠投递 outbox 契约。

### 7.2 前端交接链路浏览器 UAT

启用 `AGENT_MODE_ENABLED = true`（`chatModes.js`，原注释「恢复时改为 true」）后，在本地 Chrome 实测完整链路：

1. 登录 → Plan 模式 → 发送目标「帮我规划一个个人博客网站」。
2. 交互式需求收集：4 项约束（技术背景/发布方式/能力/部署）勾选提交。
3. 计划审查卡生成（技术栈 + 7 阶段 + 5 假设 + 1 决策）→「确认计划」审批。
4. **`switchApprovedPlanToAgent`**：点击「切换到 Agent」→ 状态 `approved → ready_for_handoff`，前端文案「Agent 模式已切换。点击下方按钮授权并启动执行。」✓
5. **`handoff.created` 事件前端显式消费**：`consumeHandoffEvent` 拉取 `listPlanEvents`，展示交接摘要「**交接已创建 · 计划 v5 · 7 个步骤 · 执行前将重新校验授权**」。✓
6. **`authorizeApprovedPlan`**：点击「授权并启动任务」→ 创建 Agent run →「任务正在执行」→ Agent 完成 →「任务已完成 / 计划已执行完成」。✓

前端改动（`ChatSession.vue`）：
- 引入 `listPlanEvents`；新增 `consumeHandoffEvent(planId, message)` 在切换成功后消费 `handoff.created` 事件，将交接快照写入 `message.handoff`。
- `serializeMessage` 序列化 `handoff` 字段（刷新后交接信息可追溯）；`restorePersistedPlanningState` 恢复 `ready_for_handoff/executing/completed` 状态时回填交接快照。
- 模板 `ready_for_handoff` 状态新增 `.planning-handoff` 交接摘要卡片。
- `chatModes.js` 的 `AGENT_MODE_ENABLED` 由 `false` 置为 `true`。

### 7.3 保留的后续风险（非本门禁范围）

1. **`confirmed_decisions` 语义**：交接快照携带**全部当前生效决策**（`user` + `assumed` + `system`）的 key→value，与 `plan_review` 卡片的 `decisions` 展示一致。若未来要严格区分「仅用户确认的决策」，可在此投影处过滤 `source == "user"`。
2. **M7-T05 交接协议**（`source_run_id`/`target_run_id`/`approved_scope`）不在本子任务范围，属 Agent 接入阶段。
3. **Agent 执行工具沙箱**：UAT 中 Agent 因工作区文件/代码执行工具未授权，退化为「产出可直接照抄执行的实施方案」而非真实生成项目文件；这是 Agent 工具授权配置项，与本 M7-T03 交接协议无关。
