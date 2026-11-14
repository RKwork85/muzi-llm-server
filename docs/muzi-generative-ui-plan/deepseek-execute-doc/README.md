# Generative UI Plan Mode · 执行报告汇总

版本：v1.0 · 生成日期：2026-11-14 · 执行范围：M1–M6

本目录是「生成式用户界面（Generative UI）plan 模式」M1–M6 阶段的执行报告，每一阶段一份独立报告。汇总结论先行，细节见各分册。

## 一、结论摘要

**plan 模式的生成式 UI（需求澄清式交互）已经完整落地，并且不破坏原有 ask 模式。** 它不是一次从零开始的实现，而是对既有 `Interactive Planning System` 的一次「只读审计 + 契约核对 + 缺口补齐 + 证据验证」。

- 前端：`ask` 与 `plan` 共用同一个对话页（`ChatView` → `ChatSession`）与同一条路由 `/chat/:id`。通过 `ModeMenu` 在对话页内部循环切换 `ask → plan → agent`，**切换视图但路由不跳转**，符合约束。
- 后端：ask 模式逻辑（`app/services/chat_service.py`、`ASK_TOOLS`）未被改动；plan 模式复用独立的 `app/planning/` 子系统，通过新增逻辑点实现，未删除任何 ask 逻辑。
- 模式切换、交互卡片、计划审阅、就地修订、版本回滚、事件日志均已实现并有单元测试覆盖。

## 二、核心事实

| 项 | 事实 |
|---|---|
| 前端对话页 | 未新建、未改写 ask 渲染逻辑；plan 组件以「增量挂载」方式接入 `ChatSession.vue` |
| 路由 | `/chat/:id`，ask/plan/agent 共用，模式切换无路由跳转 |
| 后端 ask | 未破坏；`chat_service.py` 与 `ASK_TOOLS` 保留原样 |
| 后端 plan | `app/planning/` 独立子系统，复用 ask 的 provider / 配额 / metering / 数据库 |
| 交互协议 | `muzi.ui` 等价契约（`protocol_version=1.0`），组件白名单在 `app/planning/contracts.py` |

## 三、测试证据

```text
cd muzi-llm-server
.\.venv\Scripts\python.exe -m pytest tests/planning/test_planning_contracts.py \
    tests/planning/test_planning_runtime.py tests/planning/test_revisions.py \
    tests/planning/test_action_protocol.py -q
# 54 passed in 0.29s
```

数据库集成测试需本机 PostgreSQL，以 `RUN_PLANNING_DB_TESTS=1` 显式开启（`tests/planning/test_sql_store_integration.py`），本次未在无库环境运行，状态记为待验证。

## 四、分阶段状态

| 阶段 | 目标 | 状态 |
|---|---|---|
| M1 | UI 协议、Block 模型与 Schema | CODE_PASS（交互契约严格校验 + 事件协议 + 持久化表） |
| M2 | Component Registry 与 Vue 动态渲染 | CODE_PASS（前端注册表 + 未知组件降级） |
| M3 | 模型自主选择 UI 组件 | CODE_PASS（决策引擎 + 结构化提案 + 校验修复；评测集已补齐 44 例） |
| M4 | 分段流式与事件恢复 | CODE_PASS（SSE 分段 + 事件日志序列号 + outbox） |
| M5 | Interaction Runtime | CODE_PASS（提交/跳过/审批/动作 + 幂等 + 版本 CAS） |
| M6 | 局部更新与版本控制 | CODE_PASS（就地修订 + 版本 diff + 回滚） |
| M7-T03 | Plan 接入 | DONE（需求收集 + revision + 审批 + 交接；`handoff.created` 事件落库实测 + 前端浏览器 UAT 均通过） |

> 状态语义沿用 `ACCEPTANCE_MATRIX.md`：`CODE_PASS` 表示代码与契约测试通过；`UAT_PASS`（真实浏览器端到端演示）与 `ACCEPTED`（生产评审）未在本轮完成，属后续门禁。M7-T03 已完成 `UAT_PASS`（本地 Chrome 端到端演示通过）。

## 五、设计偏差说明（诚实披露）

规划文档 `M1` 建议「通用 Block 模型（text/code/table/chart/component 混合一条消息）」与 `app/ui/` 目录结构。实际实现采用了**交互卡片模型**（`response` 文本 + `interaction` 卡片 + `plan` 快照三类字段），代码落在 `app/planning/` 而非 `app/ui/`。

- 这是**有意为之的等价实现**：plan 模式的核心诉求是「澄清需求 → 收集决策 → 审阅计划」，交互卡片模型比通用 Block 模型更贴合该场景，且避免了通用渲染器带来的 XSS/脚本注入面。
- 代价：plan 模式目前不渲染 `chart.*` / `data.table` 等分析型组件（`M1` 的图表能力）。若后续要在 plan 内叠加图表，需在 `contracts.py` 增补组件类型并映射渲染组件，属加法扩展。

## 六、本轮实际交付（变更清单）

| 文件 | 动作 | 说明 |
|---|---|---|
| `muzi-llm-server/evals/ui_selection_cases.jsonl` | 新增 | M3 评测集，44 个场景（问答/澄清/计划/图表/工具结果/拒绝/空数据/冲突/越权） |
| `muzi-llm-server/app/planning/revisions.py` | 修改 | M7-T03 新增 `build_handoff` 纯函数（交接快照投影） |
| `muzi-llm-server/app/planning/runtime.py` | 修改 | M7-T03 `switch_mode` 补 emit `handoff.created` 事件 |
| `muzi-llm-server/app/planning/sql_store.py` | 修改 | M7-T03 `switch_mode` 补 emit `handoff.created` 事件（持久化版） |
| `muzi-llm-server/tests/planning/test_plan_handoff.py` | 新增 | M7-T03 测试，10 例 |
| `muzi-llm-server/tests/planning/test_sql_store_integration.py` | 修改 | M7-T03 新增 `handoff.created` 落库集成测试（`RUN_PLANNING_DB_TESTS=1`） |
| `muzi-llm-frontend/src/modules/conversation/components/ChatSession.vue` | 修改 | M7-T03 前端显式消费 `handoff.created` 事件 + 交接摘要卡片 |
| `muzi-llm-frontend/src/modules/conversation/constants/chatModes.js` | 修改 | M7-T03 启用 `AGENT_MODE_ENABLED = true`（浏览器 UAT） |
| `docs/muzi-generative-ui-plan/deepseek-execute-doc/README.md` | 新增 | 本汇总 |
| `docs/muzi-generative-ui-plan/deepseek-execute-doc/M1_*.md` ~ `M6_*.md`、`M7-T03_*.md` | 新增 | 七份阶段执行报告 |

> M1–M6 阶段未改动任何既有 ask/plan 业务代码（评测集为纯数据文件）。M7-T03 为纯加法（新增事件 emit 与投影纯函数），未改状态机、未改权限语义、未破坏 ask/plan。

## 七、剩余风险与后续门禁

1. **前端无自动化 e2e 测试**（无 `*.spec.ts`/`*.test.ts` 项目用例），交互闭环通过后端契约测试 + 本地 Chrome 人工端到端 UAT（已覆盖 M7-T03 交接链路）保障。
2. **数据库集成测试已闭环**：`RUN_PLANNING_DB_TESTS=1` + 本机 PostgreSQL 实测，`tests/planning/test_sql_store_integration.py` 5 例通过，含新增 `test_handoff_created_event_is_persisted_and_replayable`（验证 `handoff.created` 落库到 `plan_events` 与 `plan_event_outbox`）。
3. **评测集暂无跑分器（runner）**：`ui_selection_cases.jsonl` 已就绪，但缺少一个调用 `PlanningDecisionEngine` 的评测脚本以输出「选择准确率 / 无效率 / 延迟 / Token 成本」。
4. **M7-T03 浏览器 UAT 已闭环**：`switchApprovedPlanToAgent` / `authorizeApprovedPlan` 端到端走通，`handoff.created` 事件前端显式消费（交接摘要卡片）并展示。生产评审（`ACCEPTED`）与回滚演练仍未执行。
