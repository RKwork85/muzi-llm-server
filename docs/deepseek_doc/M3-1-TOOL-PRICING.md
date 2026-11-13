# M3.1 工具计费机制

本文档定义工具如何计费。核心结论：**工具本身不直接扣额度，但外部工具的真实成本需要被定义、换算并自动记账。**

状态：**IMPLEMENTED**。

---

## 1. 工具的三类计费模型

工具按成本来源分成三类，落到 `ToolSpec` 的注册元数据里：

| 计费模型 | 含义 | 例子 | 额度影响 |
|---|---|---|---|
| `free` | 纯本地计算，无外部成本 | `calculator`、`get_current_time`、`format_json`、`mermaid` | 不直接扣额度；结果以 `tool_result` 计入下一轮输入 |
| `fixed_tokens` | 每次调用固定换算 N token | 预留：某些昂贵本地工具 | 每次调用按固定 token 扣额度 |
| `external` | 有真实货币成本，按次/按量计费 | `web_search`、`web_fetch` | 按外部报价换算成 token 扣额度，货币成本单独记账 |

目前系统里所有工具都是 `free`，只有 `web_search` / `web_fetch` 需要升级为 `external`。

---

## 2. 工具计费接口定义

### 2.1 工具注册时声明成本

在 `ToolSpec`（`app/tools/registry.py`）里增加计费元数据：

```python
@dataclass
class ToolPricing:
    model: str = "free"            # free | fixed_tokens | external
    fixed_tokens: int = 0          # fixed_tokens 模式：每次调用换算的 token 数
    external_price: str = "0"      # external 模式：单价，Decimal 字符串
    external_unit: int = 1         # 计费单位（如 1000 次请求）
    currency: str = "CNY"          # 计费币种
    requests_per_call: int = 1     # 每次工具调用产生的外部请求次数（默认 1）
```

`ToolSpec` 新增一个字段：

```python
@dataclass
class ToolSpec:
    # ... 现有字段 ...
    pricing: ToolPricing | None = None
```

注册 web_search / web_fetch 时声明：

```python
# Search 和 Fetch 都是独立 API，按请求次数计费：
#   Search  每 1000 次成功请求 = 5 元
#   Fetch   每 1000 次成功请求 = 5 元
registry.register(
    ToolSpec(
        name="web_search",
        # ...
        pricing=ToolPricing(
            model="external",
            external_price="5",
            external_unit=1000,
            currency="CNY",
            requests_per_call=1,
        ),
    )
)
```

### 2.2 计费换算函数

新增 `app/metering/tool_pricing.py`，提供统一的换算函数（用户问的「python 里自动对额度进行计算的那个函数」）：

```python
"""工具成本换算：外部货币成本 → token 额度。"""
from __future__ import annotations
from decimal import Decimal
from typing import Any


def quote_tool_tokens(pricing: ToolPricing | None, *, success: bool = True) -> int:
    """把一次工具调用换算成 token 额度。

    - free：0（结果走 tool_result 计入输入，不另扣）。
    - fixed_tokens：直接返回固定值。
    - external：外部价格换算成 token。
      换算公式：tokens = price / unit * requests_per_call / TOKEN_PRICE
      TOKEN_PRICE 是「1 token 的人民币成本」，由配置注入。
    """
    if pricing is None or pricing.model == "free":
        return 0
    if pricing.model == "fixed_tokens":
        return pricing.fixed_tokens
    if pricing.model == "external":
        if not success:
            return 0  # 失败请求不计费（按次计费口径是「成功请求」）
        price = Decimal(pricing.external_price or "0")
        unit = max(1, pricing.external_unit)
        per_call = Decimal(str(pricing.requests_per_call))
        money = price / unit * per_call
        token_price = get_token_price(pricing.currency)
        return int((money / token_price).to_integral_value(rounding="ROUND_UP"))
    return 0


def quote_tool_money(pricing: ToolPricing | None, *, success: bool = True) -> Decimal | None:
    """返回本次工具调用的真实货币成本（用于财务对账，不参与额度）。"""
    if pricing is None or pricing.model != "external" or not success:
        return None
    price = Decimal(pricing.external_price or "0")
    unit = max(1, pricing.external_unit)
    per_call = Decimal(str(pricing.requests_per_call))
    return price / unit * per_call
```

`get_token_price(currency)` 是「1 token 的人民币成本」，从配置读。这个值决定「外部成本」和「token 额度」之间的汇率，需要定义一个明确的基准价（例如按 DeepSeek 主力模型的输入输出加权价）。

### 2.3 汇率基准价配置

在配置里新增（`app/core/config.py` 或 `.env`）：

```text
TOKEN_PRICE_CNY=0.000001        # 1 token = 0.000001 元（即 100 万 token = 1 元，示例值，需按实际定价）
```

> 关键点：这个汇率必须是**显式配置**，不能硬编码在函数里，因为厂商定价会变。落地时按 DeepSeek 实际输入/输出价加权算出，写进文档注释。

---

## 3. 结算接入点

### 3.1 工具执行后自动记账

`ToolExecutor.execute()` 执行完一个工具后，需要把 `pricing` 信息带到结算。当前 `record_tool_in_new_session`（`quota_bridge.py`）已经存在，但：

1. **没有被 agent loop 调用**（agent 模式下工具执行不写 `ToolUsageRecord`）。
2. **`external_requests` / `external_cost` 没填**。

补齐方案：

```python
# persist_tool 里补写外部成本（当前写死 None 的两处）
row = ToolUsageRecord(
    # ...
    external_requests=payload.get("external_requests"),   # 本次成功请求次数
    external_cost=quote_tool_money(pricing),              # 货币成本
    currency=pricing.currency if pricing else None,
)
```

工具执行结果需要回传「是否成功 + 成功请求次数」，供结算使用。web_search 每次成功调用 = 1 次请求；web_fetch 同理。

### 3.2 外部成本是否扣额度？

两种选择，M3.1 采用**方案 A**，未来可切方案 B：

- **方案 A（推荐先做）**：外部工具成本**换算成 token 扣额度**，货币成本只写 `external_cost` 字段记账，不额外扣额度。这样额度系统保持「token 单一币种」，财务对账看 `external_cost`。
- **方案 B（未来）**：货币成本独立成账单，跟 token 额度分离。需要新开一张 `external_cost_ledger` 表，M3 阶段不做。

### 3.3 agent 循环里补写工具记录

在 `agents/loop.py` 的 `_run_tools` 里，每次工具执行完成后调用 `record_tool_in_new_session`：

```python
# 工具执行完成后（现有 executed 拿到后）
await record_tool_in_new_session(
    user_id,
    {
        "tool_call_id": call.id,
        "tool_name": call.name,
        "status": status_name,
        "duration_ms": executed.duration_ms,
        "error": error_code,
        "external_requests": 1 if spec.pricing and spec.pricing.model == "external" and error_code is None else 0,
        "external_cost": ...,
    },
    conversation_id=...,
    task_id=...,
)
```

---

## 4. web_search / web_fetch 接入示例

以 web_search 为例，完整链路：

1. **注册**：`ToolSpec` 带 `pricing=ToolPricing(model="external", external_price="5", external_unit=1000, currency="CNY", requests_per_call=1)`。
2. **执行**：`web_search` 调 DeepSeek `/v1/tools/search`，成功返回 → 计 1 次成功请求。
3. **结算**：`quote_tool_tokens` 算出本次应扣 token（`5 / 1000 * 1 / 0.000001 = 5000 token`，按示例汇率）；`quote_tool_money` 算出货币成本 `0.005 元`。
4. **记账**：`ToolUsageRecord` 写 `external_requests=1`、`external_cost=0.005`、`currency=CNY`；额度侧扣 5000 token（走 `QuotaTransaction` 或并入本轮 settle）。

> 注意：按次计费的口径是「**成功请求**」。web_search 返回 `web_auth_error` / `web_upstream_error` 等失败时不计费（`success=False` → 0）。这个口径要写进函数注释和测试。

---

## 5. 需要新增/修改的文件清单

| 文件 | 动作 |
|---|---|
| `app/tools/registry.py` | `ToolSpec` 加 `pricing` 字段；`ToolPricing` dataclass；web_search/fetch 注册时声明成本 |
| `app/metering/tool_pricing.py`（新增） | `quote_tool_tokens` / `quote_tool_money` / `get_token_price` |
| `app/metering/persist.py` | `persist_tool` 补写 `external_requests` / `external_cost` / `currency` |
| `app/agents/loop.py` | 工具执行后补写 `ToolUsageRecord` |
| `app/agents/runtime.py`（或 task_manager） | 把 user_id / task_id 传给工具记录 |
| `app/core/config.py` | 新增 `TOKEN_PRICE_CNY` 配置 |
| `.env` | 新增 `TOKEN_PRICE_CNY` |

---

## 6. 验收标准

1. web_search 一次成功调用后，`tool_usage_records` 出现一条带 `external_requests=1`、`external_cost=0.005`、`currency=CNY` 的记录。
2. 失败的 web_search（如 URL 被拒）不产生外部成本记录。
3. 外部成本换算成 token 后能正确扣减额度，且与 `TOKEN_PRICE_CNY` 配置联动（改配置，扣减变化）。
4. 非外部工具（calculator 等）`external_cost` 仍为 `None`，不额外扣额度。
