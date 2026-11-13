# M3.3 分阶段实施计划

本文档把 M3 阶段拆成可独立交付、可独立验收的小步，每步标注改动范围、依赖和验收点。

状态：**M3.0–M3.3 已实现**。

- M3.0：统一 trace 模型（加列迁移 + trace_id 派生）✅
- M3.1：工具计费机制（Search/Fetch 5 元/千次 → token 换算 + 外部成本记账）✅
- M3.2：Skill 计量接入（`persist_skill` + `record_skill_in_new_session`）✅
- M3.3：前端三模式明细（mode 分布 + 外部成本 + 技能调用展示）✅

---

## 阶段总览

| 阶段 | 名称 | 依赖 | 交付物 | 风险 |
|---|---|---|---|---|
| M3.0 | 统一 trace 模型 | 无 | 加列迁移 + trace_id 派生 | 中（涉及四张表 + 三运行时） |
| M3.1 | 工具计费机制 | M3.0 | 工具换算函数 + 外部成本记账 | 中（汇率基准需拍板） |
| M3.2 | Skill 计量接入 | M3.0 | skill 记录落库 | 低 |
| M3.3 | 前端三模式明细 | M3.0-M3.2 | 额度分析视图 | 低 |

---

## M3.0 统一 trace 模型

### 目标

四张计量表加 `trace_id` / `span_id` / `parent_span_id`，三种运行时结算都带上 trace 标识，agent/plan 补上 `persist_meters`。

### 子任务

1. **加列迁移**
   - `llm_usage_records`、`tool_usage_records`、`skill_usage_records`、`quota_transactions`、`usage_records` 加 `trace_id`（`String(64)`，索引）。
   - 前三张再加 `span_id`、`parent_span_id`（可空）。
   - 迁移脚本：已有数据 `trace_id` 回填为空（历史不追溯，从新数据开始）。

2. **trace_id 派生工具函数**
   - 新增 `app/metering/trace.py`，提供 `trace_id_for(surface, ...)` 统一派生。
   - ask：`run_id`；agent：`task_id`；plan：`public_id`；image：`request_id`。

3. **ask 接入**（已有 breakdown，只需带 trace）
   - `chat.py` `_settle_chat_usage` 增加 `trace_id` 参数。

4. **agent 接入**（补 breakdown）
   - `quota_bridge.quota_event_observer` 结算时带 `meters`，走 `persist_meters`。
   - 关键：agent 的 provider 返回要能被 `annotate_provider_usage` 解析。检查 `agents/provider_adapter.py` 的 `NormalizedCompletion` 是否带 usage 分类信息，若只有总数则先只写 `other_input/other_output`，后续再细化。

5. **plan 接入**（补 breakdown）
   - `planning/sql_store.py` `_settle_plan` 增加 `persist_meters`，`engine` 里要暴露 messages + usage 供 `build_meter` 用。

6. **image 接入**（已有 breakdown，带 trace）
   - `ark.py` 结算带 `trace_id`。

### 验收

- 三种运行时各产生一条新数据后，四张表都带正确的 `trace_id`。
- 同一个 agent task 的多个 step，`trace_id` 相同、`span_id` 不同。
- agent / plan 的轮次在额度分析里能拆出输入/输出（不再是全 `other_*`）。

---

## M3.1 工具计费机制

详见 [M3.1 工具计费机制](./M3-1-TOOL-PRICING.md)。

### 子任务

1. `ToolSpec` 加 `pricing`，新增 `ToolPricing` dataclass。
2. 新增 `app/metering/tool_pricing.py`（换算函数）。
3. `persist_tool` 补写 `external_requests` / `external_cost` / `currency`。
4. agent loop 工具执行后补写 `ToolUsageRecord`。
5. web_search / web_fetch 注册时声明 `external` 成本。
6. 配置 `TOKEN_PRICE_CNY`，拍板汇率基准。

### 前置决策

- **汇率基准 `TOKEN_PRICE_CNY` 取值**：需要用户/产品确认「1 token 的人民币成本」是多少，按 DeepSeek 实际定价加权。这个值不确认，外部成本换算就是空谈。

### 验收

- 见 M3.1 第 6 节。

---

## M3.2 Skill 计量接入

详见 [M3.2 Skill 调用与消耗链路](./M3-2-SKILL-PRICING.md)。

### 子任务

1. 从 `_insert_call` 抽出独立 `persist_skill`。
2. 新增 `record_skill_in_new_session`。
3. agent 技能激活后写 `SkillUsageRecord`。

### 验收

- 见 M3.2 第 5 节。

---

## M3.3 前端三模式明细

### 子任务

1. 额度分析接口（`usage_turns.py`）已支持 `mode` 过滤（`ask` / `plan` / `agent`），确认 agent/plan 数据补上 breakdown 后前端能正确展示。
2. 前端额度分析页增加「模式」维度的展示/筛选（如果还没做）。
3. 工具外部成本在轮次详情里展示（`external_cost` / `external_requests`）。

### 验收

- agent 的一次任务在额度分析里能看到：总 token、输入/输出拆分、每个 step 的模型调用、工具调用次数、技能调用。
- plan 的一次规划能看到：每次 revision 的模型调用明细。
- web_search 的调用能看到外部成本（`0.005 元`）标记。

---

## 实施顺序建议

```
M3.0 加列迁移（先跑通，历史不追溯）
  → M3.0 ask/image 带 trace（低成本，验证 trace 派生正确）
  → M3.0 agent/plan 补 breakdown（核心，风险最大，单独验证）
  → M3.1 工具计费（依赖 agent breakdown 链路打通）
  → M3.2 skill 计量（依赖 M3.0 trace）
  → M3.3 前端（最后）
```

每个阶段完成即 `py_compile` + `eslint` + 手工 UAT，再进入下一阶段。
