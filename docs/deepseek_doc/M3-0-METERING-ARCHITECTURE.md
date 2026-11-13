# M3.0 统一额度计量与追踪架构

本文档是 M3 阶段（统一计量）的总纲，回答三件事：

1. 现状为什么「混乱」——三套运行时的追踪口径到底差在哪。
2. 目标架构长什么样——统一 trace 模型 + 统一结算链路。
3. 三种模式（ask / agent / plan）各自的追踪路径如何落到同一套模型上。

配套文档：

- [M3.1 工具计费机制](./M3-1-TOOL-PRICING.md)
- [M3.2 Skill 调用与消耗链路](./M3-2-SKILL-PRICING.md)
- [M3.3 分阶段实施计划](./M3-3-ROADMAP.md)

状态：**M3.0 已实现**（加列迁移 `a6c9e2f5b8d1` 已落地，三运行时 trace 标识与 agent/plan breakdown 已接通）。M3.1/M3.2/M3.3 待实现。

---

## 1. 现状问题清单

先对照真实代码，把「混乱」具体化。下面是三套运行时当前各自怎么结算额度。

### 1.1 三种运行时的结算入口（现状）

| 运行时 | 结算代码位置 | `source`（UsageRecord） | `settlement_key` 格式 | 是否写 `LlmUsageRecord` breakdown |
|---|---|---|---|---|
| ask（对话） | `chat.py` `_settle_chat_usage` → `settle_in_new_session` | `chat` | `{run_id}:{轮次}` 或 `{run_id}:rest` | ✅ 是（`persist_meters`） |
| agent（智能体） | `quota_bridge.quota_event_observer` → `settle_in_new_session` | `agent` | `{task_key}:{step}` | ❌ 否（只写 `UsageRecord`，无 breakdown） |
| plan（规划） | `planning/sql_store.py` `_settle_plan` → `QuotaService.settle` | `planning` | `{request_id}:{plan_id}:{revision}` | ❌ 否（只写 `UsageRecord`） |
| image（图片） | `ark.py` → `settle_in_new_session` | `image`（走 kind） | `{request_id}` | ✅ 是（`annotate_image_usage`） |

### 1.2 三个具体问题

**问题一：没有统一 trace 标识。**

三条链路各用各的键，`run_identity.py` 只能靠「只读对照」去猜某条记录属于哪个运行时。`request_id` 在三种运行时里都存在，但没有成为主键：ask 用 `run_id`，agent 用 `task_key`，plan 用 `request_id` + `plan_id` + `revision` 拼在一起。

**问题二：agent 和 plan 没有模型调用明细（breakdown）。**

ask 和 image 都走了 `persist_meters` 写 `LlmUsageRecord` + `LlmUsageBreakdown`，能拆出输入/输出/工具 schema/技能注入等分类。agent 和 plan 只写了一条 `UsageRecord`，只有 `amount` 一个总数，无法追溯「这一轮到底花了多少输入 token、多少是工具结果、多少是技能注入」。用户看到的额度分析里，agent/plan 的轮次全是 `other_input` / `other_output`。

**问题三：工具与外部成本没打通。**

`ToolUsageRecord` 表里已经预留了 `external_requests` 和 `external_cost` 两个字段，但 `persist_tool` 里 `external_cost=None` 写死、`external_requests` 没填。web_search / web_fetch 走 DeepSeek 服务器，按「每 1000 次成功请求 5 元」计费，这笔真实成本目前既没有换算成 token，也没有记账。

---

## 2. 目标架构：统一 Trace 模型

核心思路：**把「一次用户意图」抽象成一条 trace，把意图内的每一次模型调用 / 工具调用 / 技能执行 / 摘要更新抽象成 span，全部落到同一套计量表上。**

### 2.1 三层标识

```
trace_id         一次用户意图（用户发起 → 最终结算）的唯一标识
  └─ span_id     意图内的一次具体调用（模型调用 / 工具 / 技能 / 摘要）
       └─ parent_span_id   父 span（例如：工具调用挂在触发它的模型调用下）
```

`trace_id` 的派生规则（三种运行时统一）：

| 运行时 | `trace_id` 取值 | 说明 |
|---|---|---|
| ask | `run_id`（=`client_run_id or request_id`） | 一轮对话是一个 trace，轮次是 span |
| agent | `task_id` | 一个任务是一个 trace，每个 step 是 span |
| plan | plan `public_id` | 一个 Plan 是一个 trace，每次 revision/interaction 是 span |
| image | `request_id` | 一次生成是一个 trace，单 span |

> 说明：plan 用 `public_id`（跨 revision 稳定）而不是 `request_id`（每次交互都变），这样同一个 Plan 的多次交互能归到同一条 trace 下。

### 2.2 数据模型变更

现有计量表已经基本够用，只需要**加列 + 补写**，不用新起一套表。

四张表统一新增 `trace_id`（`String(64)`，可空，建索引）：

| 表 | 现状 | 变更 |
|---|---|---|
| `llm_usage_records` | 有 `settlement_key`/`request_id`/`task_id`/`conversation_id` | 加 `trace_id`、`span_id`、`parent_span_id` |
| `tool_usage_records` | 有 `tool_call_id`/`task_id` | 加 `trace_id`、`span_id`、`parent_span_id`，补写 `external_requests`/`external_cost` |
| `skill_usage_records` | 有 `skill_execution_id` | 加 `trace_id`、`span_id`、`parent_span_id` |
| `quota_transactions` | 有 `settlement_key` | 加 `trace_id` |
| `usage_records`（rbac） | 有 `source`/`settlement_key` | 加 `trace_id` |

`settlement_key` 继续保留，作为**幂等键**（同一用户同一来源同一键只记一笔），不承担 trace 职责。

### 2.3 统一结算链路

无论哪种运行时，结算都收敛到同一个入口：

```
任意运行时
  → 收集本次调用的 meter（breakdown 分类）
  → settle_in_new_session(user_id, source, settlement_key, amount, ...)
      → QuotaService.settle()          # 写 UsageRecord，扣额度
      → persist_meters()               # 写 LlmUsageRecord + breakdown + skill + summary
      → persist_tool()                 # 写 ToolUsageRecord（含外部成本）
  → 全部带 trace_id / span_id
```

agent 和 plan 缺的只是「补上 `persist_meters` + `persist_tool` 两步」，结算主链路不用改。

---

## 3. 三种模式的追踪路径

三种模式在「触发方式」和「调用循环」上不同，但都要产出同样结构的 span。

### 3.1 ask 模式：用户主导，可切换厂商模型

```
用户发消息
  └─ trace: run_id
       ├─ span: 模型调用（provider 直返 usage，写 breakdown）      ← 已实现
       ├─ span: 工具调用（可选，工具结果回填到下一轮输入）          ← 工具 schema/结果计入输入，已实现
       └─ span: 摘要更新（离开会话时，usage_source=summary）        ← 已实现
```

要点：ask 里用户可以切换不同厂商模型（deepseek / ark），每次模型调用都要带上当次实际的 `provider` + `model` 去查价（`price_for`），不能用会话级的默认模型冒充。

### 3.2 agent 模式：Agent 自主判断，额度自动计算

```
用户提交任务
  └─ trace: task_id
       ├─ span: step 1 模型调用（agent.model.completed）            ← 已有 UsageRecord，缺 breakdown
       │    └─ span: 工具调用（web_search / web_fetch / ...）        ← 缺 ToolUsageRecord
       ├─ span: step 2 模型调用
       │    └─ span: 技能调用（discover_skills / load_skill）        ← 缺 SkillUsageRecord
       └─ ... 直到终止
```

要点：agent 的额度是「每步模型完成后自动结算」的（`quota_event_observer` 监听 `agent.model.completed` 事件），不需要用户干预。要补的是：

1. 每个 step 结算时带上 `meter`（breakdown），而不是只有 `amount`。
2. 工具执行时补写 `ToolUsageRecord`（`record_tool_in_new_session` 已经有，但当前没接到 agent loop 上）。
3. 外部工具（web_search/fetch）补写真实成本。

### 3.3 plan 模式：交互式组件，后端注册 + 前端渲染

```
用户提出目标
  └─ trace: plan public_id
       ├─ span: revision 0 模型调用（产生 plan + 组件提案）          ← 缺 breakdown
       ├─ span: interaction 1（用户交互）→ revision 1 模型调用
       └─ ... 每次 revision 都是一次模型调用 span
```

要点：plan 的区别在于「交互式组件」。模型产出的是结构化决策（`DecisionProposal`），后端在已注册的组件里匹配，前端根据返回的格式化文本自动渲染（`format_sse("interaction", ...)`）。额度结算发生在每次 revision 完成时（`_settle_plan`）。要补的是：每次 `_settle_plan` 带 meter 写 breakdown。

---

## 4. 计量字段口径统一

### 4.1 输入 token 构成（已有，三模式共用）

来自 `metering/attribute.py` 的 `INPUT_CATEGORIES`：

| 分类 | 含义 | 计费方式 |
|---|---|---|
| `system` | 系统提示词 | 计入输入 |
| `developer` | 开发者提示词（模式附加） | 计入输入 |
| `user` | 当前用户输入 | 计入输入 |
| `history` | 历史消息 | 计入输入 |
| `tool_schema` | 工具定义（JSON schema） | 计入输入 |
| `tool_result` | 工具返回结果 | 计入输入 |
| `skill_instruction` | 技能注入的指令文本 | 计入输入 |
| `retrieval` / `file` / `image` / `audio` / `video` | 检索/文件/多模态 | 计入输入 |
| `other_input` | 未归因差额 | 计入输入（provider 余数） |

### 4.2 输出 token 构成

| 分类 | 含义 | 计费方式 |
|---|---|---|
| `reasoning` | 思考 token | 计入输出 |
| `answer` | 最终回答 | 计入输出 |
| `tool_call` | 工具调用参数 | 计入输出 |
| `structured_output` | 结构化输出 | 计入输出 |
| `other_output` | 未归因差额 | 计入输出 |

### 4.3 权威值 vs 估算值

- **`provider_reported`**：供应商返回的 `prompt_tokens` / `completion_tokens` / `total_tokens`，是计费权威值，不可改。
- **`heuristic_estimated`**：本地按字符估算的分类，只用于把总额拆到各分类，差额进 `other_input` / `other_output`，绝不能反过来当账单。

这条原则已经落实在 `attribute.py`，M3 不改它，只把 agent/plan 也接进来。

---

## 5. 工具与技能的计费定位（一句话）

- **工具本身不直接扣额度**：工具结果以 `tool_result` 形式计入下一轮模型输入，额度随模型 token 走。
- **外部工具的真实成本单独记账**：web_search / web_fetch 等按次计费的，换算成 token 扣额度，同时把货币成本写进 `ToolUsageRecord.external_cost` 用于财务对账。
- **技能本身不直接扣额度**：技能指令以 `skill_instruction` 形式计入模型输入。付费技能（未来）才启用 `direct_cost`。

详细设计见 [M3.1 工具计费机制](./M3-1-TOOL-PRICING.md) 和 [M3.2 Skill 调用与消耗链路](./M3-2-SKILL-PRICING.md)。

---

## 6. 分阶段总览

| 阶段 | 内容 | 交付物 |
|---|---|---|
| M3.0 | 统一 trace 模型落地（加列 + trace_id 派生 + 三运行时补写） | 后端 schema + 迁移 |
| M3.1 | 工具计费机制（按次固定 token 换算 + 外部成本打通） | 工具计费函数 + web 工具接入 |
| M3.2 | Skill 调用与消耗链路补全 | skill 计量接入 |
| M3.3 | 前端额度分析视图支持三模式明细 | 前端展示 |

详见 [M3.3 分阶段实施计划](./M3-3-ROADMAP.md)。
