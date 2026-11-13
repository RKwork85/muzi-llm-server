# T4 / T5 设计评审

日期：2026-11-06。本文只做设计。没有业务 API、数据库或 MCP 实现，没有新增 MCP SDK，也没有安装 Node.js 或 MCP 服务。

本机在写本文时已经有 Node.js `v24.11.1` 和 `C:\Program Files\nodejs\npx.cmd`。这是环境现状，不是本次安装的。

## 1. T4 业务 API / 数据库

### 建议的只读工具

| 工具 | 作用 | 访问级别 |
|---|---|---|
| `list_business_queries` | 返回已批准的查询名称和参数说明，不返回连接串 | `read_local` |
| `run_business_query` | 按名称执行一条预先登记的查询 | `read_local` |

不提供自由 SQL、任意 HTTP、写入、删除或跨库工具。

`run_business_query` 的参数只接受：

- `name`：服务端白名单中的查询名
- `params`：与该查询声明的参数名一一对应的标量（字符串、整数、布尔）
- `limit`：调用方只能把行数上限再调低

服务端固定 SQL 或 HTTP 模板。参数使用数据库驱动的占位符，或 HTTP 客户端的独立参数字段。用户输入不能拼接进 SQL、URL 路径或命令行。

### 凭据

- 数据库连接串和业务 API token 只放在服务端环境变量或本机密钥存储。
- 不进入模型上下文、工具 schema、事件、日志或错误消息。
- 工具结果只含业务字段。连接失败返回固定错误码，不回显主机、用户名或 SQL。

### 上限与脱敏

- 只允许白名单中的查询或视图。
- 单次行数默认不超过 50，硬上限 200。
- 字段名单由查询定义固定，禁止 `SELECT *` 直通到模型。
- 超时使用现有工具 Deadline，建议单次不超过 5 秒。
- 手机号、证件号、邮箱等按查询定义脱敏后再返回。
- 结果标记 `untrusted: true`，因为业务数据同样不能当成系统指令。

### 需要用户提供后才能实现

1. 具体要开放的接口或表/视图，以及每条查询的业务含义。
2. 允许模型看到的字段和必须脱敏的字段。
3. 鉴权方式：只读账号、最小权限，以及密钥放在哪个环境变量。
4. 数据是否允许离开本进程发给 DeepSeek。默认应视为会进入模型上下文。
5. 行数、超时和是否需要审计日志。审计日志只记查询名和行数，不记行内容。

在这些信息确定前不实现 T4。

## 2. T5 MCP

MCP 工具是不可信的外部能力。描述、参数 schema 和返回值都可能含有注入。

### 接入原则

- 不绕过现有 `ToolRegistry` / `ToolExecutor`。导入时注册为普通 `ToolSpec`，`risk_level` 仍为 `low`，`access` 使用新的只读级别或沿用 `read_local`，`requires_network` 仅在该 MCP 工具会联网时为 true。
- 独立 toolset：`mcp`。默认关闭。请求不传 `toolsets` 时仍然只有 `basic`。
- 命名空间：`mcp__<server>__<tool>`。名称仍必须匹配现有 `^[A-Za-z0-9_-]{1,64}$`。超长或含其他字符的远端工具名不导入。
- 只导入配置白名单里的工具。白名单写远端工具名，不写死后端猜测的名字。连接后以 `tools/list` 的实际结果为准，名单中不存在的工具直接跳过并记一条不含参数的日志。
- 描述和 schema 视为不可信：描述截断到固定长度，去掉控制字符；schema 只保留类型、属性名和是否必填，不把远端说明原文放进系统提示。
- 结果按不可信数据处理，走现有结果长度和调用次数上限。
- 第一版客户端只实现 tools：`initialize` 或当时协议要求的握手、`tools/list`、`tools/call`。`resources`、`prompts`、`sampling`、`elicitation`、`roots`、tasks 和 subscriptions 一律不发起，收到服务端的反向请求时拒绝。
- 审批机制的前置条件：本阶段没有人工审批。因此不导入任何可能写文件、执行命令、发任意网络请求或读取环境变量的工具。

### 子进程

- 命令、参数和环境变量只来自服务端配置，不来自请求。
- 使用 `subprocess` 的参数数组启动，`shell=False`。
- 官方 README 在 Windows 上的示例是 `cmd /c npx ...`。本项目不采用这条，因为它经过 shell。Windows 上直接启动已存在的 `npx.cmd`，参数为 `["-y", "@modelcontextprotocol/server-everything"]`。找不到可执行文件就拒绝启动。
- 子进程环境只给最小集合，例如 `PATH`、`SYSTEMROOT` 和 MCP 自己的非密钥开关。不继承父进程环境。不传入 `DEEPSEEK_API_KEY`，也不设置 `GET_ENV_INCLUDE_SECRETS`。
- 生命周期放在 FastAPI lifespan：启动时拉起，关闭时先关 stdin，再等待短超时，超时后终止进程。崩溃后标记 `mcp` toolset 不可用，不在请求路径里无限重启。卡住的调用由现有工具超时结束；超时后该次调用返回失败，进程若不再响应则在 lifespan 关闭时清理。

## 3. 风险与里程碑

| 风险 | 处理 |
|---|---|
| `get-env` 返回全部环境变量名，并在 `GET_ENV_INCLUDE_SECRETS=1` 时返回明文密钥 | 不把密钥传给子进程；白名单排除 `get-env` |
| `gzip-file-as-resource` 会按调用方 URL 抓取数据 | 白名单排除；补充阶段不设置 `GZIP_ALLOW_PRIVATE_IPS` |
| 采样、表单和 URL elicitation 会反向调用客户端 | 第一版不声明这些能力，并拒绝反向请求 |
| 工具描述注入 | 截断、过滤，权限仍由 toolset 决定 |
| 官方 Windows 示例使用 `cmd /c` | 改为直接启动 `npx.cmd`，不经 shell |
| 协议同时存在 2025 握手和 2026-07-28 `server/discover` | 补充阶段先实现 2025 `initialize` + tools。新协议单独评审 |

建议拆分：

1. 进程内假 MCP，只覆盖握手、列工具、调用、错误、超时和崩溃。
2. 配置、白名单、命名空间和 toolset 开关。
3. 用户明确授权后，再用 Everything 做一次只读 live。

## 4. MCP 补充阶段测试方案

本 Phase 不执行本方案。以下内容供 Phase 3 结束后单独补充。真实 Everything 进程需要用户再次授权后才能启动。

### 对照过的官方材料

2026-11-06 读取：

- npm 页面显示 `@modelcontextprotocol/server-everything` 版本 `2.2.1`（页面更新时间 2026-11-04）
- [Everything README](https://github.com/modelcontextprotocol/servers/blob/main/src/everything/README.md)
- [Server Features](https://github.com/modelcontextprotocol/servers/blob/main/src/everything/docs/features.md)
- [Startup](https://github.com/modelcontextprotocol/servers/blob/main/src/everything/docs/startup.md)
- [MCP Inspector](https://modelcontextprotocol.io/docs/2026-07-28/tools/inspector)

README 写明：默认 `npx @modelcontextprotocol/server-everything` 使用 stdio；也可以显式加参数 `stdio`。通过 `npx` 运行时要求 Node.js 24 或更新。本机现有版本满足这条，但仍不在本次启动它。

Features 文档当前列出的工具包括 `echo`、`get-sum`、`get-env`、`gzip-file-as-resource` 等。这些名字只作为 2026-11-06 的文档快照。实现和测试不得把它们写死。连接后调用 `tools/list`，只使用实际返回且位于白名单中的名字。若文档中的 `echo` 或 `get-sum` 不在返回列表里，live 测试失败并停止，不改去调用别的工具。

建议的 live 白名单只有两个无副作用工具：文档快照中的 `echo` 和 `get-sum`。排除 `get-env`、`gzip-file-as-resource`、采样、elicitation、资源抓取和长任务工具。

### 启动配置

服务端配置示例（不是请求字段）：

```text
MCP_ENABLED=false
MCP_SERVER_NAME=everything
MCP_COMMAND=C:\Program Files\nodejs\npx.cmd
MCP_ARGS=-y,@modelcontextprotocol/server-everything
MCP_TOOL_ALLOWLIST=
```

`MCP_ENABLED` 默认 false。`MCP_TOOL_ALLOWLIST` 在授权 live 时再填 `tools/list` 确认过的名字。命令和参数来自配置，测试代码不能拼接用户消息。

独立验证服务端时，可以用官方 Inspector，把它和本项目客户端的问题分开：

```text
npx @modelcontextprotocol/inspector --cli --method tools/list -- npx @modelcontextprotocol/server-everything
```

Inspector 文档写的是 CLI 用 `--method tools/list`。实际参数以运行 `npx @modelcontextprotocol/inspector --help` 的输出为准。Inspector 本身也要用户授权后才运行。

### 离线假服务

用 Python 写一个只读假 MCP：stdio 上的换行分隔 JSON-RPC，只回答 `initialize`、`tools/list` 和 `tools/call`。提供两个无副作用工具，名称在假服务里可以是 `echo` 和 `add`，但客户端测试仍从 `tools/list` 读取名字，断言不依赖源码里的字面量常量以外的第二份名单。

假服务覆盖：

- 握手成功后才能 `tools/list`
- 调用白名单工具返回不可信文本
- 未知工具、非法 JSON、超时、子进程退出码非 0
- 服务端发来 `resources/read`、`prompts/get`、`sampling/createMessage`、`elicitation/create` 或 `roots/list` 时客户端拒绝
- 子进程环境中不存在 `DEEPSEEK_API_KEY`
- 请求体里的命令字段被忽略，实际启动命令仍是配置值

这些测试默认运行，不访问网络，不启动 Node。

### 真实 Everything

默认 skip。只有用户把授权开关设上，并且 Node 仍满足 README 的 24+ 要求时才运行。步骤：

1. 用配置启动 stdio 进程，不经 shell。
2. `tools/list`，记录实际工具名。
3. 若白名单中的两个工具都在列表里，各调用一次：回显一条固定测试字符串，以及对 `1` 和 `2` 求和。
4. 断言结果里没有 API Key，事件里没有工具正文以外的环境变量。
5. 关闭进程并确认它已退出。

任一步与文档快照不一致，就记录实际返回并停止，不临时扩大白名单。

### 依赖

补充阶段若使用官方 Python MCP SDK，需要新增依赖并在当时的 `DEVIATIONS.md` 论证。当前 `requirements.txt` 不包含该 SDK。备选是只实现上述三个 JSON-RPC 方法的极小客户端，不引入 SDK。倾向在补充阶段先用极小客户端完成假服务测试；只有握手和 tools 调用无法稳定表达时，才申请加入 SDK。

## 5. 需要用户决定

1. T4 是否要做，以及具体的只读查询、字段、脱敏和鉴权。
2. MCP 补充阶段是否授权安装或使用已有 Node 启动 Everything。本次发现本机已有 Node `v24.11.1`，但没有启动服务。
3. 补充阶段使用极小 JSON-RPC 客户端，还是批准新增官方 MCP SDK。
4. live 白名单是否只保留 `tools/list` 确认后的回显和求和工具。
5. `mcp` toolset 即使实现后，是否继续默认关闭。本文建议默认关闭。
