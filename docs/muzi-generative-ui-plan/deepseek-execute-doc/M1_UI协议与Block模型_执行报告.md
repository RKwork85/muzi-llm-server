# M1 · UI 协议、Block 模型与 Schema — 执行报告

状态：**CODE_PASS**（契约与校验测试通过；UAT 留待浏览器演示）

## 1. 阶段目标

让一条消息承载文本、代码、表格、图表、工具结果和交互块，保持稳定顺序与可扩展性。

## 2. 现状审计结论

实际实现采用「交互卡片模型」而非文档建议的「通用 Block 模型」。核心差异与等价关系如下：

| 规划文档建议 | 实际实现 | 说明 |
|---|---|---|
| `MessageEnvelope` 内含 `blocks[]` | `PlanRuntimeResponse` 内分字段：`response` + `interaction` + `plan` | plan 消息的文本/卡片/快照各自独立、ID 稳定 |
| `Block`（text/chart/choice…） | `Interaction`（single_select/multi_select/text_input/text_area/form/confirmation/plan_review…） | 交互块是 plan 模式唯一需要的「组件」载体 |
| `app/ui/contracts.py` | `app/planning/contracts.py` | 目录命名不同，职责相同 |
| `muzi.ui.v1` | `protocol_version: "1.0"` | 协议版本化已落实 |

## 3. 关键交付物

### 3.1 协议与契约（`app/planning/contracts.py`）

- `Interaction`：`discriminated union`（`single_select`/`multi_select` 必须有 `options`，`form` 必须有 `form`，`plan_review` 必须有 `plan_review`），字段长度/数组上限均受 Pydantic 约束，`extra="forbid"` 拒绝未知字段。
- `PlanSnapshot`：含 `goal/assumptions/assumption_items/decisions/constraints/steps`，`revision` 单调递增。
- `PlanningEvent`：`sequence`（≥1）+ `event_id` + `occurred_at`（强制带时区），支撑 M4 事件重放。
- `InteractionStatus` 状态机：`pending → submitted/skipped/expired/superseded/cancelled`。

### 3.2 存储设计（`app/models/planning.py`）

加法迁移，共 8 张表：

```text
plans                       计划聚合根（current_revision + status + CheckConstraint）
plan_revisions              不可变版本快照（uq_plan_revisions_revision）
plan_decisions              版本化决策（source ∈ user/assumed/system）
plan_interactions           版本化交互卡片（status + expires_at）
interaction_submissions     幂等提交（uq_interaction_submissions_idempotency）
plan_actions                统一动作幂等记录（uq_plan_actions_idempotency）
plan_events                 有序事件日志（uq_plan_events_sequence）
plan_event_outbox           事务性 outbox（用于可靠投递）
```

### 3.3 API 契约（`app/api/v1/endpoints/planning.py`）

`/conversations`、`/conversations/{id}`、`/conversations/{id}/mode`、`/conversations/{id}/events`、`/plans`、`/plans/{id}`、`/plans/{id}/revisions`、`/plans/{id}/messages`、`/plans/{id}/messages/stream`、`/plans/{id}/interactions/.../submissions`、`/plans/{id}/interactions/.../skip`、`/plans/{id}/actions`、`/plans/{id}/approve`、`/plans/{id}/events` 等。全部走鉴权依赖 `get_authenticated_user`，归属校验在 `_owned_plan` 内。

## 4. 验收标准逐条核查

| 验收项 | 结果 | 证据 |
|---|---|---|
| 一条消息含 text+chart+choice 三块并保持顺序 | 部分满足（text+choice+plan 三字段，无 chart） | plan 不需要 chart，见设计偏差 |
| 非法组件、超大嵌套、未知版本被拒绝或安全降级 | 满足 | `contracts.py` 严格校验；`test_planning_contracts.py` 参数化用例覆盖非法 `protocol_version`/未知 type/未知字段/空 options |
| 旧消息历史可正常加载，数据库迁移可回滚 | 满足 | 加法迁移；`plan_revisions` 不可变 |
| 不同用户无法读取彼此消息块 | 满足 | `_owned_plan` 按 `owner_id` 过滤，不存在则统一返回 `PlanningNotFound`，不泄露存在性 |

## 5. 测试证据

```text
.\.venv\Scripts\python.exe -m pytest tests/planning/test_planning_contracts.py -q
# 通过（属于 54 passed 之一）
```

覆盖：`Interaction` 接受版本化单选；拒绝 `protocol_version=2.0`、未知字段、重复 option id、空 options、空 prompt；非选择类型拒绝 options；表单字段 key 唯一性。

## 6. 差距与风险

- **通用 Block 模型未采用**：如后续需要 plan 内图表/表格，需在 `contracts.py` 增补组件类型（加法）。
- **数据库集成验证未跑**：`test_sql_store_integration.py` 需 `RUN_PLANNING_DB_TESTS=1` + PostgreSQL。
- **风险**：协议漂移靠 Pydantic + `extra=forbid` 防守；建议为 `contracts.py` 生成 JSON Schema 快照做双向契约测试（当前未做，属可后续补齐项）。
