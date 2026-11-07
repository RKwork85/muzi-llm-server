# 02 Mermaid 工具设计（T1b）

## 1. 设计目标

用户需要"流程信息图"。核心设计问题是：**图由谁保证正确？**

| 环节 | 负责方 | 原因 |
|---|---|---|
| 决定画什么：节点内容、流程逻辑、措辞 | 模型 | 语义理解和内容组织，是模型的强项 |
| 生成语法正确、可渲染的 Mermaid 源码 | **本地工具（确定性）** | 模型直接写 Mermaid 容易出现语法错误、保留字冲突（如节点 ID 为 `end`）、特殊字符未转义 |
| 安全（无脚本、无外链回调、无配置注入） | **本地工具** | 模型输出不可信，渲染端会执行 Mermaid 的部分指令 |
| 画面渲染 | 客户端（默认）/ 服务端（可选，默认关闭） | 见 §7 |

因此首选路径是：**模型提供结构化数据，工具生成 Mermaid**。`validate_mermaid` 只是为模型已经直接写出的源码提供兜底检查。

## 2. 工具总览

| 工具 | toolset | risk_level | 网络 | 说明 |
|---|---|---|---|---|
| `create_flowchart` | `diagram` | `read_local` | 否 | 结构化 → Mermaid flowchart |
| `create_sequence_diagram` | `diagram` | `read_local` | 否 | 结构化 → Mermaid sequenceDiagram |
| `validate_mermaid` | `diagram` | `read_local` | 否 | 校验并规范化模型直接写的源码 |

三者都写成 async handler，内联执行，输入有硬上限，纯函数，无 IO。`max_calls_per_run` 建议 `create_*` 各 ≤ 5，`validate_mermaid` ≤ 5。

函数名使用 snake_case、≤ 64 字符，并按 DeepSeek 官方文档/当前 SDK 对 function name 的约束核对。

## 3. `create_flowchart`

### 参数（Pydantic，`extra="forbid"`）

```text
direction: "TB" | "LR" | "BT" | "RL"           默认 "TB"
title: str | None                               ≤ 80 字符，仅作元数据，不写入 Mermaid 源码
nodes: list[Node]                               1..60
  Node: key: str   (^[A-Za-z][A-Za-z0-9_]{0,31}$，请求内唯一)
        label: str (1..80 字符)
        shape: "rect" | "round" | "stadium" | "diamond" | "circle"   默认 "rect"
edges: list[Edge]                               0..120
  Edge: source: str (必须是已有 node key)
        target: str (同上)
        label: str | None (≤ 60 字符)
        style: "solid" | "dotted" | "thick"     默认 "solid"
```

校验：`key` 唯一；`edges` 引用的 key 必须存在；节点数、边数、标签长度超限直接拒绝；不接受任何额外字段。

### 生成规则（确定性）

1. 输出首行 `flowchart <direction>`。
2. **用生成的内部 ID**（`n1`、`n2`…，按 `nodes` 顺序）代替用户 `key`，用户 `key` 只用于解析边。这样可彻底规避保留字（如 `end`）和特殊字符问题。
3. 节点：`n1["label"]`（rect）、`n1("label")`（round）、`n1(["label"])`（stadium）、`n1{"label"}`（diamond）、`n1(("label"))`（circle）。**所有标签一律使用双引号形式。**
4. 边：solid `-->`、dotted `-.->`、thick `==>`；带标签用 `-->|"label"|` 形式。
5. 标签转义：控制字符删除，换行替换为空格；`"` → `#quot;`，`<` → `#lt;`，`>` → `#gt;`，`&` → `#amp;`。**以上实体写法和各形状/边的具体语法必须对照 Mermaid 官方文档核对，并用测试固化；不得凭记忆假设。**
6. 不输出 `click`、`href`、`style`/`classDef`（本阶段不支持自定义样式，减少攻击面）、不输出 init 指令或 frontmatter。
7. 缩进、换行、顺序固定，同一输入输出逐字节一致。
8. 孤立节点（无任何边）允许，但在 `warnings` 中提示。

### 返回

```json
{
  "diagram_type": "flowchart",
  "mermaid": "flowchart TB\n  n1[\"开始\"]\n  ...",
  "title": "用户登录流程",
  "node_count": 5,
  "edge_count": 5,
  "warnings": [],
  "artifact_ids": ["art_1"]
}
```

同时产出一个 `type: "mermaid"` 的 artifact（见框架文档 §6）。

## 4. `create_sequence_diagram`

```text
participants: list[Participant]    1..20
  Participant: key (同 key 规则), label (1..60)
messages: list[Message]            1..100
  Message: source, target (必须是已有 participant key)
           text: str (1..120)
           kind: "sync" | "async" | "reply"     默认 "sync"
title: str | None
```

- 首行 `sequenceDiagram`；参与者 `participant p1 as <label>`，同样使用内部 ID。
- `sync` → `->>`；`async` → `-)`；`reply` → `-->>`（具体箭头语法须对照官方文档核对）。
- 消息文本转义：除 §3 的规则外，**序列图里 `;` 和 `#` 有特殊含义**，须按 Mermaid 实体规则转义；用测试覆盖包含这些字符的文本。
- 不输出 `Note`、`activate`、`box`、`link` 等扩展语法（本阶段不支持）。

返回结构同 §3，`diagram_type: "sequenceDiagram"`。

## 5. `validate_mermaid`

用于模型**直接写了 Mermaid 源码**的情况。

### 参数

```text
source: str    1..20000 字符
```

### 检查（词法/结构层面，纯 Python）

拒绝（`valid=false`，`issues` 列出原因类别，不回显可疑片段全文）：

1. 超长、含 NUL 或非法控制字符。
2. 首个有效语句不在白名单：`flowchart`、`graph`、`sequenceDiagram`、`stateDiagram-v2`、`classDiagram`、`erDiagram`。
3. 含 `%%{`（init/配置指令）、YAML frontmatter（以 `---` 开头的配置块）。
4. 含 `click`、`href`、`callback`、`link`、`links` 等交互/外链语句。
5. 含 `javascript:`、`data:` URL、`<script`、`<iframe`、`<img`、`<a `、`on\w+\s*=` 等 HTML/脚本迹象。
6. 行数超限（如 > 400）。

通过后返回：

```json
{
  "valid": true,
  "diagram_type": "flowchart",
  "normalized": "<统一换行、去尾空白、去 BOM 后的源码>",
  "line_count": 12,
  "issues": [],
  "warnings": ["flowchart 中使用了节点 ID 'end'，可能导致渲染失败"],
  "checked": "lexical_safety_and_structure",
  "guarantees_render": false
}
```

### 必须诚实的边界

- 纯 Python 的校验**不是 Mermaid 语法解析器**。`valid=true` 的含义是"通过安全与基本结构检查"，**不保证能渲染成功**。必须用 `guarantees_render: false` 明示，并写入工具描述，避免模型向用户承诺"保证可渲染"。
- 如需真正语法校验，只有两条路：用 Mermaid 官方解析器（Node 侧），或客户端渲染时的错误回报。这属于可选增强，见 §7，默认关闭，不得在未获授权时引入 Node 依赖。
- 对常见高频错误（节点 ID 为保留字 `end`、未闭合的引号/括号）做启发式 `warnings` 即可，不要伪装成完整解析。

## 6. 工具描述（给模型看的 description）要点

- 画图时**优先使用 `create_flowchart` / `create_sequence_diagram`**，传入结构化节点与边。
- 仅当结构化工具无法表达时，才直接写 Mermaid 并调用 `validate_mermaid`。
- 不要自行承诺渲染结果；产物以 artifact 返回。
- 工具描述需简短，避免占用过多上下文。

## 7. 服务端渲染（默认关闭，不实现）

可选的 `render_mermaid_svg` 需要 Node + mermaid-cli + 无头浏览器，攻击面和运维成本都显著上升。本 Skill **不实现**，只在 `docs/phase3/DEVIATIONS.md` 或设计文档中写明：

- 默认由客户端渲染 Mermaid（使用 strict 安全级别，不开启 HTML 标签）。
- 若未来启用服务端渲染：独立 toolset、默认关闭、`subprocess` 不经 shell、固定参数、无网络、临时目录、CPU/内存/时间上限、输出 SVG 再做净化、限制并发。需用户明确授权后另立里程碑。

## 8. 测试要求（全部离线）

1. 同一输入多次输出逐字节相同（快照测试）。
2. 节点 key 为 `end`、`graph`、`class`、中文、emoji 时，输出仍可由 §3 规则稳定生成（内部 ID 生效）。
3. 标签含 `"`、`<script>`、`&`、`|`、`;`、`#`、换行、反引号、超长文本：被转义或拒绝，输出中不出现未转义的原样危险片段。
4. 边引用不存在的 key、重复 key、超过节点/边上限、额外字段：被拒绝。
5. `validate_mermaid`：逐条覆盖 §5 的拒绝项；合法示例通过；`guarantees_render` 恒为 false。
6. 输出始终是合法 JSON；Mermaid 源码超过 artifact 上限时结构化失败而非截断后标成功。
7. E2E（mock Provider）：用户请求画流程图 → 模型发出 `create_flowchart` tool call → 工具执行 → tool message 配对 → 模型最终答复 → `AgentResult.artifacts` 含对应 Mermaid。
8. 默认请求（不传 `toolsets`）看不到、也调用不了这些工具；模型伪造调用会被拒绝并回传失败结果。
9. 事件流中 artifact 只含类型与大小，不含正文。

## 9. 能力归属记录（供 3.2 问题使用）

在 `docs/phase3/CAPABILITY_ATTRIBUTION.md` 里维护下表，T1 先填"设计分工"，T6 填"实测结果"：

| 能力 | 谁提供 | 证据（事件/日志/测试） |
|---|---|---|
| 理解需求、拆解流程步骤 | 模型 | 实测：模型发出的 tool_call 参数 |
| 生成合法 Mermaid 源码 | 本地工具 | 快照测试 + 事件中的 tool_call_completed |
| 安全过滤 | 本地工具 | 注入测试 |
| 图像渲染 | 客户端 | 非服务端能力 |

结论的判定方式：在 `toolsets=["basic","diagram"]` 下，事件流中出现 `create_flowchart` 调用且响应含 artifact，说明 Mermaid 源码来自**本地工具**；在 `toolsets=["basic"]` 下模型仍可能直接输出 Mermaid 文本，那是**模型自身的文本生成**，二者不同，须分别记录。
