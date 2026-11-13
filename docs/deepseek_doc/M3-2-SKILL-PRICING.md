# M3.2 Skill 调用与消耗链路

本文档梳理 Skill 在系统中如何被调用、如何产生消耗，以及如何补齐计量。

状态：**IMPLEMENTED**。

---

## 1. Skill 现在的调用方式

Skill 不是独立入口，而是**通过工具暴露给模型**的。在 `app/tools/registry.py` 里注册了三个 skill 工具：

| 工具 | 作用 |
|---|---|
| `discover_skills` | 列出可用技能，模型先调用它发现技能 |
| `load_skill` | 加载某个技能的完整说明（SKILL.md 正文），读进上下文 |
| `read_skill_resource` | 读技能包内引用的资源文件 |

调用链（agent 模式）：

```
agent loop
  → 模型判断需要技能 → 调用 discover_skills 工具
  → 调用 load_skill 工具，把 SKILL.md 正文加载进上下文
  → 后续模型调用时，技能正文作为 skill_instruction 计入输入 token
```

ask 模式下，技能由 `chat_service.prepare_stream` 里的 `_apply_registered_skills` 在**请求发起前**注入，`prepared.skill_text` 记录注入的指令文本，`skill_refs` 记录技能标识。

### 1.1 当前消耗归属

技能的消耗**不是独立扣额度**，而是：

1. **指令 token**：技能正文（SKILL.md）作为 `skill_instruction` 计入模型输入 token（见 `attribute.py` 的 `INPUT_CATEGORIES`）。
2. **技能内部如果调工具**：工具结果作为 `tool_result` 计入输入，工具外部成本走 [M3.1](./M3-1-TOOL-PRICING.md)。

所以「技能消耗」本质上是「模型输入的一部分」，这和 `SkillUsageRecord` 表注释一致：

> `SkillUsageRecord` 注释：「Skill 注入与执行。指令 Token 已计入模型输入，这里不另扣额度。」

---

## 2. 当前缺口

### 2.1 agent 模式没有写 `SkillUsageRecord`

`SkillUsageRecord` 表已存在，但 `persist_meters` 里写 skill 记录的逻辑依赖 `meter["skills"]`，而 agent 模式根本没走 `persist_meters`。所以：

- **ask 模式**：`build_meter` 会产出 `skills` 列表 → `persist_meters` 写 `SkillUsageRecord`。✅ 已实现
- **agent 模式**：只写 `UsageRecord`，不写 `SkillUsageRecord`。❌ 缺失

### 2.2 skill 执行状态未关联 trace

`SkillOrchestrator`（`app/skills/orchestrator.py`）已经记录了 `activation_ids` / `skill_ids` / `versions` / `duration_ms`，但这些信息**没有落到计量表**，只进了日志。

---

## 3. 目标链路

统一后，一次技能调用的完整消耗链路：

```
模型调用 span（llm_usage_records）
  ├─ skill_instruction 分类：SKILL.md 正文的 token 估算    ← 已计入输入
  └─ 关联的 skill_usage_records：
       ├─ skill_id / skill_name / skill_version
       ├─ instruction_tokens（估算）
       ├─ status / duration_ms
       └─ direct_cost（付费技能才用，当前 None）
```

### 3.1 agent 模式补写

在 agent 的结算链路上，把 `SkillOrchestrator` 的结果带进 meter：

- agent loop 的每个 step，`quota_event_observer` 结算时，除了 `amount` 还带上 `meters`（含 `skills` 列表）。
- 或者更简单：`SkillOrchestrator.prepare()` 完成后，直接调用一个 `record_skill_in_new_session`（仿照 `record_tool_in_new_session`），把技能激活记录落库，不必等模型结算。

推荐后者，理由：技能激活（`prepare`）和模型调用（`step`）是两个独立事件，分开记更清晰，且技能激活可能早于模型调用发生。

### 3.2 付费技能（未来）

`SkillUsageRecord.direct_cost` 字段已预留，语义是「付费技能的直接成本」。当前所有技能 `direct_cost=None`。未来若有付费技能：

- 在技能包的 frontmatter 里声明价格（类似工具的 `ToolPricing`）。
- `SkillOrchestrator` 加载时解析价格，`persist_skill` 时写入 `direct_cost`。

M3 阶段只做「免费技能计量」，付费技能留到后续。

---

## 4. 需要新增/修改的文件清单

| 文件 | 动作 |
|---|---|
| `app/metering/persist.py` | 抽出独立的 `persist_skill` 函数（现在 skill 写死在 `_insert_call` 里） |
| `app/authorization/application/quota_bridge.py` | 新增 `record_skill_in_new_session`（仿照 `record_tool_in_new_session`） |
| `app/agents/runtime.py` 或 `loop.py` | 技能激活后调用 `record_skill_in_new_session` |
| `app/skills/orchestrator.py` | `OrchestratorResult` 已含技能信息，确认能透出给计量层 |

---

## 5. 验收标准

1. agent 模式下，一次 `load_skill` 后，`skill_usage_records` 出现对应技能记录，`skill_name` 正确。
2. `instruction_tokens` 有值（估算），`direct_cost=None`（免费技能）。
3. 同一次技能激活不会重复记录（幂等，按 `skill_execution_id` 去重）。
4. ask 模式的技能记录不受影响（回归验证）。
