# M3 统一额度计量 —— 交付收尾总结

本文档是 M3 阶段（统一额度计量与追踪）的交付收尾总结，记录四个子阶段（M3.0–M3.3）的目标、产出、关键改动与验证结果，作为后续维护和扩展的索引。

状态：**M3.0–M3.3 全部完成**（后端 + 前端 + 测试）。

---

## 1. 总目标回顾

M3 阶段要解决的核心问题是「额度系统混乱」：三套运行时（ask / agent / plan，外加 image）追踪口径不统一、缺少统一 trace 标识、agent 与 plan 没有模型调用明细（breakdown）、外部工具真实成本未打通。

统一后形成一条清晰的计费原则：

```
一次用户意图 = 一条 trace
  ├─ 模型调用   → LlmUsageRecord + LlmUsageBreakdown（输入/输出分类）
  ├─ 工具调用   → ToolUsageRecord（含外部成本 external_cost）
  ├─ 技能执行   → SkillUsageRecord（指令 token 计入输入，不另扣额度）
  └─ 额度扣减   → UsageRecord + QuotaTransaction（统一幂等键 settlement_key）
```

配套文档：

- [M3.0 统一额度计量与追踪架构](./M3-0-METERING-ARCHITECTURE.md)
- [M3.1 工具计费机制](./M3-1-TOOL-PRICING.md)
- [M3.2 Skill 调用与消耗链路](./M3-2-SKILL-PRICING.md)
- [M3.3 分阶段实施计划](./M3-3-ROADMAP.md)

---

## 2. 各阶段交付内容

### 2.1 M3.0 统一 trace 模型

**目标**：四张计量表加 `trace_id` / `span_id` / `parent_span_id`，三种运行时结算都带 trace 标识，agent/plan 补上 breakdown。

**产出**：

| 文件 | 改动 |
|---|---|
| `alembic/versions/a6c9e2f5b8d1_unified_trace.py` | 加列迁移：`trace_id`（5 张表）+ `span_id`/`parent_span_id`（3 张表）+ 索引 |
| `app/metering/trace.py` | `new_span_id()` 等 span 派生工具 |
| `app/agents/loop.py` | usage 累积 `reasoning_tokens` / `cache_read_tokens` / `cache_write_tokens`，`agent.model.completed` 事件带 model/output_text/cache 指标 |
| `app/agents/events.py` | `_ALLOWED_FIELDS` 放行 reasoning/cache/model/provider/output_text 等字段 |
| `app/authorization/application/quota_bridge.py` | `_agent_meter()`：每个 agent step 结算时带 meter，写 `LlmUsageRecord` + breakdown |
| `app/planning/runtime.py` | `PlanningDecisionEngine` 追踪 reasoning/cache/messages/output_text |
| `app/planning/sql_store.py` | `_persist_plan_meter()`：plan revision 结算写 meter（`runtime_mode="plan"`） |

**trace_id 派生口径**：

| 运行时 | trace_id |
|---|---|
| ask | `run_id` |
| agent | `task_id` |
| plan | plan `public_id` |
| image | `request_id` |

### 2.2 M3.1 工具计费机制

**目标**：Search/Fetch 按「每 1000 次成功请求 5 元」定义外部成本，换算成 token 扣额度，同时把货币成本记账。

**产出**：

| 文件 | 改动 |
|---|---|
| `app/tools/registry.py` | 新增 `ToolPricing` dataclass；`ToolSpec` 加 `pricing` 字段；`web_search`/`web_fetch` 注册为 `external`（5 元 / 1000 次 / CNY） |
| `app/metering/tool_pricing.py` | `quote_tool_money()`（货币成本）、`quote_tool_tokens()`（换算 token）、`get_token_price()`（读 `TOKEN_PRICE_CNY`） |
| `app/metering/persist.py` | `persist_tool` 补写 `external_requests` / `external_cost` / `currency` |
| `app/agents/loop.py` | 工具执行后带 external 字段发事件 |
| `app/authorization/application/quota_bridge.py` | `_observe_tool()`：写 `ToolUsageRecord` + 外部成本 token 扣额度（`source="tool"`） |
| `app/services/chat_service.py` | `resource` 事件带 external 字段 + `_tool_succeeded()` 成功判定 |
| `app/api/v1/endpoints/chat.py` | 落库 + 扣额度 |

**计费模型**（`ToolPricing.model`）：

| model | 含义 | 额度影响 |
|---|---|---|
| `free` | 纯本地，无外部成本 | 不直接扣额度（结果走 `tool_result` 计入输入） |
| `fixed_tokens` | 每次调用固定 token | 每次调用扣 `fixed_tokens` |
| `external` | 外部按次计费 | 按成功请求换算 token 扣额度 + 货币成本记账 |

**换算公式**：`tokens = price / unit × requests_per_call / TOKEN_PRICE_CNY`（向上取整）。实测：一次成功搜索 = 0.005 元 = 5000 token（默认 `TOKEN_PRICE_CNY=0.000001` 时）。

### 2.3 M3.2 Skill 调用与消耗链路

**目标**：agent 模式的技能激活写入 `SkillUsageRecord`，指令 token 估算并归入输入。

**产出**：

| 文件 | 改动 |
|---|---|
| `app/metering/persist.py` | 抽出独立 `persist_skill()`（原 skill 写死在 `_insert_call` 内） |
| `app/authorization/application/quota_bridge.py` | 新增 `record_skill_in_new_session()` |
| `app/agents/runtime.py` | `_apply_registered_skills()` 返回 `OrchestratorResult`；新增 `_record_skill_usage()`，技能激活后写记录（`instruction_tokens` 用 `estimate_tokens` 粗估，`direct_cost=None`） |

**幂等**：按 `skill_execution_id`（=`activation_id`）去重，同一次激活不会重复记。

### 2.4 M3.3 前端三模式明细视图

**目标**：额度分析页展示 ask/plan/agent 模式分布、工具外部成本、技能调用。

**产出**：

| 文件 | 改动 |
|---|---|
| `app/authorization/application/usage_turns.py` | `mode` 过滤（已有，确认）；`summary.by_mode` 三模式 token/轮次分布；`turn.skill_count`；工具 external 字段进映射与时间线；技能记录支持按 `task_id` 关联到轮次 |
| `app/schemas/usage_analysis.py` | 新增 `UsageModeStat` + `UsageSummary.by_mode` + `UsageTurn.skill_count` |
| `muzi-llm-frontend/.../UsageStats.vue` | 新增「运行模式分布」区块（Ask/Plan/Agent 横向条 + token + 条数） |
| `muzi-llm-frontend/.../UsageTurnCard.vue` | 轮次卡片新增「技能」指标 |
| `muzi-llm-frontend/.../usage.css` | 模式分布面板样式 |

---

## 3. 最终数据链路

```
ask / agent / plan / image 任一运行时
  → settle_in_new_session(source, settlement_key, amount, trace_id, ...)
      ├─ QuotaService.settle()    # UsageRecord（rbac）+ 额度扣减，幂等键 settlement_key
      ├─ persist_meters()         # LlmUsageRecord + LlmUsageBreakdown + SkillUsageRecord + SummaryUpdate
      ├─ persist_tool()           # ToolUsageRecord（含 external_requests/external_cost/currency）
      └─ persist_skill()          # SkillUsageRecord（agent 独立链路，指令 token 估算）
  → 全部记录带 trace_id / span_id / parent_span_id
```

---

## 4. 验证结果

- **后端单测**：新增 `tests/test_tool_pricing.py`（7 用例，覆盖 free / fixed_tokens / external / 失败不计费 / 配置读取）。
- **回归测试**：`tests/test_tools.py` + `tests/agents/` + `tests/skills/` + 用量分析相关 + 日志/身份相关全部通过。
- **非 DB 全量**：`398 passed, 9 skipped, 0 failed`（DB 集成测试 `tests/database`、`tests/api`、`tests/planning` 需 PostgreSQL，未在本轮跑）。
- **前端**：`eslint` 通过。

**过程中一并修复的既有问题**：

1. `admin.py` 5 个 `status_code=204` 端点用 `-> None` 返回导致 FastAPI 断言失败（`reset-password` / `delete_user` / `delete_user_conversations` / `delete_user_quotas` / `delete_role`），改为 `-> Response` + `return Response(status_code=204)`。
2. `app/authorization/application/execution_guard.py`、`app/sms/receipts.py`、`app/sms/sender.py` 的子 logger 名（`muzi_llm.tool_auth` / `muzi_llm.sms`）统一为 `muzi_llm`。
3. `tests/test_run_identity.py` 的 plan 结算键断言随 M3.0 内联变量改动同步更新。

---

## 5. 遗留事项与后续建议

1. **`TOKEN_PRICE_CNY` 汇率基准待拍板**：当前 `app/metering/tool_pricing.py` 里 `get_token_price()` 默认回退 `0.000001`（占位值）。这个值决定外部工具换算成 token 的数量，需要按 DeepSeek 实际 token 定价加权确定，写入 `.env` 的 `TOKEN_PRICE_CNY`。
2. **OSS 上传工具尚未计费**：`deliver_file` / `upload_to_oss` / `upload_files_to_oss` 目前 `pricing=None`（不扣额度）。若要按「固定次数调用扣 token」，只需给这三个 `ToolSpec` 加 `pricing=ToolPricing(model="fixed_tokens", fixed_tokens=N)`，详见下文「扩展指南」。
3. **付费技能**：`SkillUsageRecord.direct_cost` 字段已预留，当前所有技能 `direct_cost=None`，付费技能留待后续。
4. **DB 集成测试**：`tests/database` / `tests/api` / `tests/planning` 需连接 PostgreSQL 后单独跑一轮。

---

## 6. 扩展指南：给任意工具加计费

给一个工具加计费，只需改它 `ToolSpec` 注册处的一行 `pricing`，计费链路（换算 + 扣额度 + 记账）已在 M3.1 打通，无需再动结算代码：

```python
from app.tools.registry import ToolPricing  # registry.py 内定义，直接引用

# 1) 每次调用固定扣 N token
ToolSpec(
    name="xxx",
    ...,
    pricing=ToolPricing(model="fixed_tokens", fixed_tokens=5000),
)

# 2) 外部按次计费（成功请求才计费，换算 + 记账）
ToolSpec(
    name="web_search",
    ...,
    pricing=ToolPricing(
        model="external",
        external_price="5",
        external_unit=1000,
        currency="CNY",
        requests_per_call=1,
    ),
)
```

三种 `model` 的行为差异：

| model | 是否依赖成功 | 额度扣减 | external_cost 记账 |
|---|---|---|---|
| `free` | 否 | 不扣 | 无 |
| `fixed_tokens` | 否（调用即扣） | 每次扣 `fixed_tokens` | 无 |
| `external` | 是（成功才计） | 换算 token | 有（货币成本） |

> 注意：`fixed_tokens` 是「每次调用即扣」，无论本次执行成功或失败都会扣固定 token；若想「只有成功才扣」，需要在 `quote_tool_tokens` 里对 `fixed_tokens` 也传入并判断 `success`。
