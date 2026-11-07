# qs_llm_dp

DeepSeek 接入服务。代码版本见 `app/__init__.py`，当前是 `0.1.0`。

它提供两条互不调用的路径：

- 对话：`ChatService` 使用只含 `calculator` 和 `get_current_time` 的注册表。
- Agent：`AgentRuntime` 使用完整注册表。模型只能调用本次请求允许的工具，服务端执行后把结果交回模型。

两条路径共用 DeepSeek Provider、提示词和服务配置。HTTP 层不拼 SDK 请求，Provider 不保存业务提示词。

## 目录

```text
app/
  main.py              # 入口、生命周期、异常和 request_id
  api/                 # 健康检查和 /api/v1
  core/                # 配置、日志、错误码、SSE
  agents/              # 任务、循环、策略、产物
  services/            # 对话编排、提示词、第一阶段工具执行
  providers/           # AsyncOpenAI 适配
  tools/               # 工具注册和各工具实现
  data/                # 表格处理、任务文件、上传客户端
  prompts/system/      # default、calculator、json_assistant
  schemas/
```

配置从环境变量和本机 `.env` 读取。`.env.example` 只有占位符。

## 环境

建议 Python 3.11 及以上。

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

在 `.env` 中填写 `DEEPSEEK_API_KEY`。不要把真实密钥提交到仓库。

| 变量 | 作用 | 默认 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 上游密钥。为空时进程可以启动，调用模型时返回 `CONFIGURATION_ERROR` | 空 |
| `DEEPSEEK_BASE_URL` | OpenAI SDK 的 base URL，不要带 `/chat/completions` | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 默认模型，必须在白名单内 | `deepseek-flash` |
| `DEEPSEEK_MODEL_ALLOWLIST` | 允许的模型，逗号分隔 | `deepseek-flash,deepseek-v4-pro` |
| `DEEPSEEK_TIMEOUT_SECONDS` | 上游超时 | `60` |
| `DEEPSEEK_MAX_RETRIES` | 只对超时、连接失败和 5xx 重试 | `2` |
| `DEEPSEEK_THINKING` | `enabled` 或 `disabled`。本服务默认显式关闭 | `disabled` |
| `EXPOSE_REASONING` | 是否把思维链返回给客户端 | `false` |
| `MAX_TOOL_ROUNDS` | 对话接口默认可执行的工具轮数 | `3` |
| `TOOL_ROUND_HARD_LIMIT` | 对话接口轮数硬上限，最大 5 | `5` |
| `TOOL_TIMEOUT_SECONDS` | 第一阶段单个工具超时 | `2` |
| `TOOL_RESULT_MAX_CHARS` | 回传给模型的工具结果长度上限 | `4000` |
| `REQUIRE_API_KEY_ON_STARTUP` | 为 `true` 且没有 Key 时拒绝加载配置 | `false` |
| `LOG_LEVEL` | 日志级别 | `INFO` |
| `AGENT_MAX_ACTIVE_TASKS` | 同时存在的 Agent 任务数 | `8` |
| `AGENT_TASK_TTL_SECONDS` | 内存任务保留时间。不删除数据文件 | `300` |
| `TOOLS_NETWORK_ENABLED` | 是否允许 `research` | `false` |
| `TOOLS_WORKSPACE_ROOT` | 只读工作区。不能是项目根 | 空 |
| `DATA_STORE_ROOT` | 数据处理目录。不能是项目根 | `data_store` |
| `DATA_FILE_TTL_SECONDS` | 数据文件保留时间 | `3600` |
| `DATA_DOWNLOAD_TTL_SECONDS` | 本机下载令牌保留时间 | `600` |
| `OSS_UPLOAD_BASE_URL` | 上传服务地址。空则上传工具返回 `OSS_UNAVAILABLE` | 空 |
| `OSS_UPLOAD_PREFIX` | 上传路径前缀 | `agent-data` |
| `OSS_UPLOAD_TIMEOUT_SECONDS` | 上传超时 | `20` |

其余字段和取值范围见 `app/core/config.py` 与 `.env.example`。改 `.env` 后需要重启进程。

缺少 Key 时，响应只有「模型服务配置不完整」，不会回显变量值。日志会去掉 API Key 和 `Authorization: Bearer`。

## 启动

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- Swagger：<http://127.0.0.1:8000/docs>
- OpenAPI：<http://127.0.0.1:8000/openapi.json>
- 存活检查：<http://127.0.0.1:8000/health>
- 就绪检查：<http://127.0.0.1:8000/ready>（只看是否配置了 Key，不请求 DeepSeek）

应用启动时创建 AsyncOpenAI 客户端，并在关闭时释放。SDK 自带重试已关闭。数据目录不是项目根时会创建目录，并清理其中已过期的文件。

## 错误

```json
{
  "error": {
    "code": "UPSTREAM_TIMEOUT",
    "message": "模型服务请求超时",
    "request_id": "..."
  }
}
```

常见错误码：`CONFIGURATION_ERROR`、`UPSTREAM_AUTH_ERROR`、`UPSTREAM_RATE_LIMIT`、`UPSTREAM_TIMEOUT`、`UPSTREAM_ERROR`、`INVALID_MODEL_RESPONSE`、`INVALID_TOOL_ARGUMENTS`、`TOOL_NOT_ALLOWED`、`TOOL_EXECUTION_ERROR`、`TOOL_ROUND_LIMIT`、`REQUEST_VALIDATION_ERROR`、`MODEL_NOT_ALLOWED`、`PROMPT_NOT_FOUND`、`PROMPT_VARIABLE_MISSING`、`AGENT_TASK_NOT_FOUND`、`AGENT_TASK_LIMIT`、`INTERNAL_ERROR`。策略拒绝使用 `AgentPolicyRejectedError` 中的具体代码。未知下载令牌是 `ARTIFACT_NOT_FOUND`（404），过期令牌是 `DOWNLOAD_EXPIRED`（410）。

请求校验失败一律返回 `REQUEST_VALIDATION_ERROR`，响应里不包含字段细节。

## 对话接口

客户端消息只允许 `user` 和 `assistant`。`system` 由 `prompt_name` 选择 `app/prompts/system/` 下的模板。`tool` 消息只由服务端写入。请求体不能自带可执行工具。

### 非流式

`POST /api/v1/chat/completions`

```powershell
$body = @{
  messages = @(@{ role = "user"; content = "解释一下 FastAPI 的依赖注入" })
  prompt_name = "default"
  temperature = 0.7
} | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/chat/completions -ContentType "application/json; charset=utf-8" -Body $body
```

### 流式

`POST /api/v1/chat/completions/stream`

媒体类型为 `text/event-stream`。正常完成的顺序是 `delta`、可选的 `usage`、最后一条 `done`。`done` 和 `error` 不会出现在同一次响应里。流开始前的错误是普通 JSON。流开始后 HTTP 保持 200，上游失败变成 `event: error`。

### JSON 模式

`response_format.type` 为 `json_object` 时，系统提示词或用户消息里必须出现 `json`，最终内容必须能被 `json.loads` 解析。模板 `json_assistant` 需要变量 `task`。

### 受控工具调用

`POST /api/v1/chat/tool-calls`

这个接口只有两个工具：

- `calculator`：AST 白名单算术，不使用 `eval` 或 `exec`
- `get_current_time`：当前 UTC 时间

模型返回 `tool_calls` 后，服务端校验工具名和参数，执行处理函数，再以 `role=tool` 回传。默认最多 3 轮，硬上限 5。

`GET /api/v1/models` 返回配置白名单，不表示上游的全部模型。

## Agent 运行

`POST /api/v1/agents/runs` 只创建任务并立刻返回 `task_id`。此时 `status` 通常是 `created`，`answer` 仍为空。执行在后台进行。

用 `GET /api/v1/agents/runs/{task_id}` 查询，直到 `status` 变为 `completed`、`failed`、`cancelled` 或 `limit_reached`。取消用 `POST /api/v1/agents/runs/{task_id}/cancel`。事件流是 `GET /api/v1/agents/runs/{task_id}/events`。

请求字段是 `message`、`prompt_name`，以及可选的 `toolsets`、`allowed_tools`、`policy`。省略 `toolsets` 时只有 `basic`。未知工具集是 `TOOLSET_UNKNOWN`，条件不满足的工具集是 `TOOLSET_UNAVAILABLE`。`policy` 只能把限额收紧，不能放宽。

一次运行默认最多 3 次工具调用、4 步，同一次模型响应里最多 3 个工具调用。硬上限分别是 8、8 和 5。请求不能提高这些默认值。模型返回正文并结束时，服务端把正文保存为 `answer`，不会再从正文里补一次工具调用。

```powershell
$body = @{
  message = "画一个用户登录流程图"
  prompt_name = "default"
  toolsets = @("basic", "diagram")
} | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/agents/runs -ContentType "application/json; charset=utf-8" -Body $body
```

没有产物时响应不含 `artifacts`。有图或文件时才出现。事件里只有产物的 id、类型和大小，没有源码或文件字节。

| toolset | 工具 | 何时可用 |
|---|---|---|
| `basic` | `calculator`、`get_current_time` | 始终。省略 `toolsets` 时只用这一组 |
| `utility` | `format_json`、`text_stats`、`date_diff` | 始终 |
| `diagram` | `create_flowchart`、`create_sequence_diagram`、`validate_mermaid` | 始终。只生成 Mermaid 源码，不渲染 |
| `research` | `web_search`、`web_fetch` | `TOOLS_NETWORK_ENABLED=true` 且已配置 DeepSeek Key。抓取由 DeepSeek 服务器执行 |
| `workspace` | `list_directory`、`read_text_file`、`search_text` | `TOOLS_WORKSPACE_ROOT` 是真实目录，且不是项目根 |
| `data` | 见下一节 | `DATA_STORE_ROOT` 是真实目录，且不是项目根 |

`create_flowchart` 接收节点和边，不接收一段现成的 Mermaid 文本。生成的源码放在 `artifacts[].content`，类型是 `text/vnd.mermaid`。模型下一轮只看到产物摘要。

## 数据处理和文件

`data` 工具集：

- 读入和变换：`import_dataset`、`clean_data`、`deduplicate_data`、`normalize_data`、`classify_data`、`validate_data`
- 导出和打包：`export_json`、`export_csv`、`export_excel`、`export_markdown`、`create_package`、`list_package_files`
- 交付：`get_download_url`、`upload_to_oss`、`upload_files_to_oss`

`import_dataset` 只接受 JSON 或 CSV 文本，或工作区内的相对路径。变换不改源表，每次返回新的数据集引用。工具结果只含引用、行数、列名和少量样例。

导出的文件写在 `data_store/tasks/{task_id}/files/{file_id}`，磁盘名是文件编号。`get_download_url` 返回 `/api/v1/files/downloads/{token}`。`GET` 这个路径可下载文件。应用日志把令牌记成 `[redacted]`。

`OSS_UPLOAD_BASE_URL` 有值时，`upload_to_oss` 调用上传服务的 `/upload/single`，`upload_files_to_oss` 调用 `/upload/multiple`。返回地址只采用响应里的 `url`。本服务不保存对象存储 AccessKey，也不调用 `/upload/text` 或 `/upload/batch_from_local`。

默认 3 次工具调用放不下「导入、变换、导出、再下载或上传」整条链。后一步必须看到前一步返回的 id，不能在同一次模型响应里排完这条依赖链。

## 边界

- 不执行模型给出的任意代码，不注册 Shell。
- 不提供任意路径的写入、删除或移动。数据文件只写在数据目录内由服务端生成的 id 下。工作区工具只读。
- 外部 HTTP 有三条：对话补全、`research` 触发的搜索和抓取、配置了上传地址时的文件上传。
- 工具参数先做 JSON 和 Pydantic 校验，未知字段拒绝。
- 日志记录 request id、路径、状态、耗时、模型和 usage，不记录消息正文和密钥。
- 没有登录、计费、长期记忆、数据库、Redis、Celery、MCP、RAG 或前端界面。
- 没有 Mermaid 服务端渲染。要看到图，由客户端渲染 `artifacts` 或回答正文里的源码。
