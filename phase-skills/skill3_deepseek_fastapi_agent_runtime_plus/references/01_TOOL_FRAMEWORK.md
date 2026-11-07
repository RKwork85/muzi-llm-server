# 01 工具框架契约（T0）

本文是对现有 ToolRegistry/ToolExecutor 的**扩展约束**。若现有代码结构与此不同，以真实代码为准，适配后在 `DEVIATIONS.md` 说明。

## 1. 工具元数据

在现有工具定义上增加字段，全部带向后兼容默认值，使 `calculator`、`get_current_time` 无需改动即可注册。

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `risk_level` | enum: `read_local` / `read_network` / `read_files` / `write_local` / `write_external` | `read_local` | 本 Phase 只允许前三种；后两种注册时直接拒绝 |
| `requires_network` | bool | false | true 时受 `TOOLS_NETWORK_ENABLED` 控制 |
| `timeout_s` | float | 沿用现有 | 不得超过 ExecutionPolicy 的每工具上限 |
| `max_result_chars` | int | 沿用现有 | 结构化截断，见 §5 |
| `max_calls_per_run` | int \| None | None | 超限返回结构化失败结果 |
| `idempotent` | bool | true | 本 Phase 工具均应为只读，故为 true；仍须声明 |
| `toolset` | str | `basic` | 所属工具集 |

规则：`write_local` / `write_external` 在注册时抛出明确错误。本 Phase 不存在审批流程，也不实现。

## 2. Toolset

服务端维护 toolset → 工具集合的映射，请求只能按名称选择。

| toolset | 工具 | 可用条件 |
|---|---|---|
| `basic`（默认） | `calculator`、`get_current_time` | 始终 |
| `diagram` | `create_flowchart`、`create_sequence_diagram`、`validate_mermaid`，以及 T1a 的本地工具 | 始终（无网络、无文件） |
| `research` | `web_search`、`web_fetch` | `TOOLS_NETWORK_ENABLED=true` 且已配置 DeepSeek API Key（Search/Fetch 复用同一个 Key） |
| `workspace` | `list_directory`、`read_text_file`、`search_text` | `TOOLS_WORKSPACE_ROOT` 已配置且合法 |

- `ExecutionPolicy.allowed_tools` = 请求所选 toolset 的并集 ∩ 服务端当前可用工具。请求不能放宽。
- 未知 toolset、当前不可用的 toolset：返回明确的 4xx 错误，使用**新增**错误码（如 `TOOLSET_UNKNOWN`、`TOOLSET_UNAVAILABLE`），沿用项目现有错误响应格式；不改任何既有错误码。
- 不得因工具不可用而静默降级为 `basic`。

## 3. Agent API 变更（仅限可选新增）

- 请求：新增可选字段 `toolsets: list[str]`，默认 `["basic"]`，元素数量有上限。仍然禁止客户端传入工具实现、handler、schema。
- 响应：新增可选字段 `artifacts`（见 §6）。
- 未传新字段时，响应与 M3 完全一致。须有测试逐字段比对（快照或字段集断言）。

## 4. 执行规则

1. **async 优先**：联网工具必须使用 async 客户端（`httpx.AsyncClient`），超时取剩余 Deadline 与工具 `timeout_s` 的较小值。
2. **同步线程池**仅服务既有同步工具。有上限的纯计算（Mermaid 生成、JSON 格式化）写成 async handler 内联运行，不占用 `max_workers=2` 的池，也不会因超时堆积线程。
3. 网络类工具使用独立信号量闸门（配置项，默认小值，如 4；硬上限必须存在）。排队等待时间计入 Deadline。
4. 每个 `tool_call_id` 仍只执行一次；工具失败也必须回传配对的 tool message。
5. 不对任何工具做自动重试。
6. 取消传播 `CancelledError`；HTTP 请求在取消时必须被关闭，响应流必须释放。

## 5. 结果与截断

- 工具结果必须始终是合法 JSON 对象。
- 超限时结构化截断（缩短 `text` 之类的大字段，保留元数据），并设 `truncated: true`、`original_chars`；不得切字符串后仍标记为完整成功。
- 所有来自外部的内容放在明确命名的字段（如 `untrusted_content`），并附 `source`。

## 6. Artifacts 通道

目的：让 API 调用方**确定性**拿到图表等产物，而不依赖模型在最终回答里原样复述。

```text
Artifact {
  id: str                 # 运行内唯一
  type: "mermaid"         # 本 Phase 仅此一种，枚举可扩展
  title: str | None
  content: str            # 受限长度（默认 ≤ 20000 字符）
  content_type: "text/vnd.mermaid"
  source_tool: str
}
```

- 由工具结果中的 `artifacts` 提取，Runtime 汇总进 `AgentResult.artifacts`。
- 每次运行 artifact 数量（默认 ≤ 5）和总大小有硬上限，超限返回结构化失败。
- 发给模型的 tool message 只含**摘要和 artifact id**以及必要的简短内容，不必重复完整正文；是否包含完整 Mermaid 源码由 T1 设计决定并记录理由。
- 事件流（`agent.tool.completed` 等）只带 artifact 的 `type`、`size`、`id`，**不带正文**。
- artifact 内容属于模型/工具输出，API 消费方需按不可信内容渲染（Mermaid 渲染端使用 strict 安全级别）。

## 7. 事件与日志

- 沿用现有事件名，不新增破坏性事件。如需新增（如 `agent.artifact.created`），必须是新增且不影响既有客户端的事件，并说明。
- 日志只记录工具名、耗时、结果大小、是否截断、错误类别。不记录完整参数和正文。网络工具可记录目标**主机名**，不记录完整 URL 的查询串。

## 8. T4/T5 设计评审文档要求

`docs/phase3/T4_T5_DESIGN.md` 必须包含：

1. **T4 业务 API / 数据库**：拟定的工具接口草案；只读边界；认证凭据存放（不进入模型上下文）；数据库只允许参数化的白名单查询或视图，不开放自由 SQL；行数/字段/超时上限；脱敏；需要用户提供的信息清单（具体接口、数据范围、鉴权方式）。
2. **T5 MCP**：MCP 工具视为不可信外部能力；命名空间前缀；仅白名单导入；工具描述也可能含注入，需过滤；不得绕过 ToolRegistry/Executor；连接与超时策略；审批机制前置条件。
3. 风险清单、建议的里程碑拆分、**需要用户做出的决定**。
4. **"MCP 补充阶段"测试方案**（本 Phase 不实现，只写方案，供 Phase 3 结束后单独补充）：
   - 测试对象：官方 Everything 测试服务（npm 包 `@modelcontextprotocol/server-everything`，stdio 方式启动；Windows 下命令名可能是 `npx.cmd`）。写方案时先核对其官方 README 与当前版本，不得凭记忆写启动参数和工具名。
   - 备选：自写一个极小的只读 MCP 服务（两个无副作用工具，如 echo、加法），不联网、不碰文件；若需要引入 MCP Python SDK，必须在方案中列为新增依赖并论证。
   - 工具名**不得写死**：以连接后 `tools/list` 实际返回为准（不同版本的求和工具名不同）。
   - 客户端第一版**只支持 tools**；对 resources、prompts、sampling、elicitation、roots 等能力明确拒绝或忽略，并有测试。
   - 启动命令、参数、环境变量**只来自服务端配置**，绝不来自请求；不经 shell；子进程只传最小环境变量，**不得把 DeepSeek Key 等密钥传给 MCP 子进程**。
   - 命名空间：导入的工具统一加前缀（如 `mcp__<server>__<tool>`），仅导入白名单内的工具；工具描述与参数 schema 视为不可信，限制长度并过滤。
   - 独立 toolset（如 `mcp`），默认关闭；仍走现有 ToolRegistry/ToolExecutor，受 Deadline、超时、结果大小、调用次数、并发上限约束；MCP 结果按不可信数据处理。
   - 子进程生命周期：由 lifespan 启动与关闭，崩溃、超时、卡死的处理策略，关闭时清理。
   - 环境前置：是否已安装 Node.js（`npx` 可用）；**Cursor 不得擅自安装系统级软件**，需向用户说明并等待确认。
   - 测试：离线用假 MCP 服务（进程内或本地 stdio 假实现）覆盖协议握手、列工具、调用、错误、超时、崩溃；真实 Everything 服务测试需用户明确授权，默认 skip。
   - 可用 MCP 官方调试工具独立验证服务端本身（以官方文档为准），以区分"服务端问题"与"本项目客户端问题"。

写完后停止，等待用户决定。
