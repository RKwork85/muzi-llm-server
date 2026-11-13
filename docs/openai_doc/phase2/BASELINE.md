# Phase 1 基线（M0）

核查日期：2026-11-06。依据是 `qs-test/qs_llm_dp` 的当前源代码、已安装依赖和当场运行的测试，不是 README 转述。

Phase 1 版本号是 `app/__init__.py` 中的 `0.1.0`。父仓库分支为 `main`。`git status` 显示整个 `qs-test/qs_llm_dp/` 仍是未跟踪目录，这段实现没有 Git 提交历史。

## 1. 当前目录

生产代码和测试（不含 `.venv`、缓存）：

```text
qs_llm_dp/
  app/
    __init__.py                      # __version__ = 0.1.0
    main.py                          # create_app、lifespan、中间件、异常处理
    api/
      deps.py
      errors.py                      # OpenAPI 错误登记
      health.py                      # GET /health、/ready、/
      v1/router.py
      v1/endpoints/chat.py
      v1/endpoints/models.py
    core/
      config.py
      context.py                     # request_id
      exceptions.py
      logging.py
      sse.py
    schemas/
      common.py
      chat.py
    services/
      chat_service.py
      prompt_service.py
      tool_service.py
    providers/
      base.py
      deepseek.py
      manager.py
      parsing.py
    prompts/system/
      default.txt
      calculator.txt
      json_assistant.txt
    tools/
      registry.py
      schemas.py
      calculator.py
      clock.py
  tests/
    conftest.py
    fakes.py
    test_chat.py
    test_errors.py
    test_health.py
    test_live_deepseek.py
    test_prompt.py
    test_secrets.py
    test_settings.py
    test_sse.py
    test_tools.py
  requirements.txt
  pytest.ini
  .env.example
  README.md
  phase-skills/deepseek-fastapi-phase2-skill/
```

本文件和 `IMPLEMENTATION_PLAN.md` 是 M0 新增的分析文档，不是运行时代码。

## 2. 依赖与运行环境

`requirements.txt` 只给下限。当前虚拟环境实际版本：

| 包 | 版本 |
|---|---|
| Python | 3.13.11 |
| fastapi | 0.162.0 |
| uvicorn | 0.54.0 |
| openai | 3.46.0 |
| pydantic | 2.14.3 |
| pydantic-settings | 2.16.2 |
| httpx | 0.28.1 |
| pytest | 9.2.0 |
| pytest-asyncio | 1.4.1.post1 |

`pytest.ini` 使用 `asyncio_mode = auto`，`testpaths = tests`。测试通过 `make_settings()` 显式传入全部配置并设置 `_env_file=None`，不读取本机 `.env`。

配置来自环境变量和 `qs_llm_dp/.env`。`Settings` 的 `extra="ignore"`。API Key 字段 `repr=False`。`.gitignore` 忽略 `.env`。`.env.example` 的 `DEEPSEEK_API_KEY` 为空。

## 3. 模块职责

| 模块 | 职责 | 明确不做的事 |
|---|---|---|
| `api/v1/endpoints/chat.py` | 接收请求、流开始前调用 `ensure_ready`、返回 JSON 或 `StreamingResponse` | 不拼 SDK 参数，不执行工具 |
| `ChatService` | 渲染提示词、校验模型白名单、非流式补全、工具轮次循环、SSE 迭代 | 不创建 HTTP 客户端 |
| `PromptService` | 按 `prompt_name` 读取 `prompts/system/{name}.txt`，替换 `{{var}}` | 不接受客户端传入的 system 正文 |
| `ToolRegistry` / `ToolSpec` | 注册工具名、描述、Pydantic 参数模型、同步 handler、`risk_level` | 只允许 `risk_level="low"` |
| `ToolService` | 生成 OpenAI tools schema、校验参数、`to_thread` 执行、截断结果 | 不重试，不并行 |
| `DeepSeekProvider` | 复用一个 `AsyncOpenAI`，关闭 SDK 重试，自行重试超时、连接失败和 5xx | 不保存提示词，不执行工具 |
| `ProviderManager` | lifespan 中 `startup` / `aclose` | 无 Key 时不创建客户端 |
| `parsing.py` | 把 SDK 响应收成 `CompletionResult` / `StreamChunk` | 流式解析不读取 `tool_calls` |
| `exceptions.py` | 固定错误码和安全中文文案 | 不把上游异常字符串返回给客户端 |
| `format_sse` | `event: {name}\ndata: {一行 JSON}\n\n` | 不解释业务事件 |

现有路由：

- `POST /api/v1/chat/completions`
- `POST /api/v1/chat/completions/stream`
- `POST /api/v1/chat/tool-calls`
- `GET /api/v1/models`
- `GET /health`
- `GET /ready`

## 4. 实际调用链

非流式对话：

```text
POST /api/v1/chat/completions
  -> ChatService.complete
  -> prepare_chat（系统提示词 + user/assistant 消息）
  -> DeepSeekProvider.create_chat_completion
  -> parse_completion
  -> ChatCompletionResponse
```

`stream=true` 会在进入 Provider 之前返回 `422 REQUEST_VALIDATION_ERROR`。

流式对话：

```text
POST /api/v1/chat/completions/stream
  -> prepare_chat
  -> provider.ensure_ready
  -> StreamingResponse(ChatService.iter_sse)
  -> DeepSeekProvider.stream_chat_completion
  -> parse_stream_chunk
  -> format_sse
```

流开始前的 400、404、422、500 是 JSON。流开始后的上游失败是 HTTP 200 加 `event: error`。正常结束是 `event: done`。客户端断开时停止迭代并 `aclose` 上游流。`request_id` 在 `prepare_chat` 时写入 `PreparedChat`，因为中间件会在响应体开始前重置 ContextVar。

工具调用：

```text
POST /api/v1/chat/tool-calls
  -> ToolService.schemas_for
  -> prepare_chat(tools, tool_choice="auto")
  -> loop:
       create_chat_completion
       无 tool_calls -> 返回最终回答
       有 tool_calls -> 轮次 +1
       超过 max_rounds -> TOOL_ROUND_LIMIT，本轮不执行
       否则追加 assistant 消息和 role=tool 结果
```

`/chat/completions` 和 `/stream` 不向 Provider 传递 `tools`，因此不会执行工具。`calculator.txt` 只是系统提示，不能单独触发工具执行。

lifespan：

```text
create_app
  -> ProviderManager.startup
  -> PromptService
  -> build_default_registry
  -> ToolService
  -> ChatService
  -> yield
  -> ProviderManager.shutdown -> DeepSeekProvider.aclose
```

无 Key 时进程可以启动，`/ready` 返回 `not_ready/missing_api_key`，模型调用返回 `CONFIGURATION_ERROR`。

## 5. Provider 接口

`LLMProvider` 协议只有四个方法：

- `create_chat_completion(request) -> CompletionResult`
- `stream_chat_completion(request)`，异步迭代 `StreamChunk`
- `aclose()`
- `startup()`

`DeepSeekProvider.ensure_ready()` 不在协议里。流式端点用 `getattr` 调用它。

`ProviderChatRequest` 字段：`model`、`messages`、`temperature`、`max_tokens`、`thinking`、`reasoning_effort`、`response_format`、`tools`、`tool_choice`。没有 deadline、取消令牌或按次超时。超时来自 `AsyncOpenAI(..., timeout=settings.deepseek_timeout_seconds)`，默认 60 秒。

`CompletionResult` 含 `content`、`reasoning_content`、`tool_calls`、`finish_reason`、`usage`。`ToolCall` 是 `id`、`name`、`arguments` 字符串。

`StreamChunk` 只有正文、思维链、finish、usage、model、id。`parse_stream_chunk` 不解析 delta 里的 `tool_calls`。只有工具增量、没有正文的分片会被丢弃。

SDK 调用：

- `base_url` 不得以 `/chat/completions` 结尾。实际请求由 SDK 拼接。
- `max_retries=0`。`_call` 只对 `APITimeoutError`、`APIConnectionError` 和状态码大于等于 500 重试，次数为 `deepseek_max_retries + 1`。401、403、429 和 4xx 不重试。
- `thinking` 放入 `extra_body`。流式额外设置 `stream_options.include_usage=true`。
- `_call` 只捕获 `Exception`。`CancelledError` 不会被吞掉。

`reasoning_content` 的回传发生在 `ChatService._assistant_message`：有工具调用时，若该字段不是 `None`，就放进下一次 assistant 消息。`content is None` 会被写成空字符串。

## 6. 工具机制

注册表在 `app/tools/registry.py`。启动时只注册：

- `calculator`：`CalculatorArgs.expression`，AST 白名单，禁止 `eval` / `exec`
- `get_current_time`：`EmptyArgs`，返回 UTC 时间

`ToolSpec.handler` 的类型是同步 `Callable[[Any], str]`。没有 `enabled`、单工具超时或单工具输出上限。超时和截断使用全局 `tool_timeout_seconds`（默认 2）和 `tool_result_max_chars`（默认 4000，下限 96）。

`ToolService.execute` 的行为：

1. 一次最多 5 个 tool call，超出或为空则 `INVALID_MODEL_RESPONSE`。
2. 先校验全部调用，再按顺序执行。任一调用失败会抛错，后续调用不执行。
3. `tool_call_id` 必须匹配 `^[A-Za-z0-9_-]{1,128}$`。
4. 参数必须是 JSON 对象。Pydantic `extra="forbid"`。空字符串是非法 JSON。`{}` 对无参工具有效。
5. 未知工具或非 low 风险工具是 `TOOL_NOT_ALLOWED`。
6. handler 经 `asyncio.to_thread` 执行，没有单独的有界线程池。
7. 结果包成 `{"source":"server_tool","name","result"}`。超长时截断 `result`，保持合法 JSON。
8. 不重试，不并行。

`parameters_schema()` 去掉 `title`、`description`、`$schema`，并设置 `additionalProperties: false`。没有设置 Beta `strict: true`，也没有把 base URL 改到 `/beta`。

工具轮次：请求 `max_tool_rounds` 为 1 到 5；缺省用 `MAX_TOOL_ROUNDS`（3），再被 `TOOL_ROUND_HARD_LIMIT`（最大 5）截断。超限时本轮工具不执行。这里计算的是模型返回 tool call 的轮数，不是 tool call 总个数。

## 7. Prompt、Schema、SSE 和错误

提示词文件名就是 `prompt_name`。现有模板：`default`、`calculator`、`json_assistant`。客户端消息只允许 `user` 和 `assistant`，列表长度 1 到 50，单条正文 1 到 32000 字符。`json_assistant` 需要变量 `task`。

Phase 1 SSE 事件名只有 `delta`、`usage`、`error`、`done`。带 usage 的分片先发 `usage`，再发该分片的 `delta`。`done` 与 `error` 互斥。`EXPOSE_REASONING=false` 时，`delta` 不含 `reasoning_content`。

错误响应：

```json
{"error": {"code": "...", "message": "...", "request_id": "..."}}
```

已有码：`CONFIGURATION_ERROR`、`UPSTREAM_AUTH_ERROR`、`UPSTREAM_RATE_LIMIT`、`UPSTREAM_TIMEOUT`、`UPSTREAM_ERROR`、`INVALID_MODEL_RESPONSE`、`INVALID_TOOL_ARGUMENTS`、`TOOL_NOT_ALLOWED`、`TOOL_EXECUTION_ERROR`、`TOOL_ROUND_LIMIT`、`INTERNAL_ERROR`、`MODEL_NOT_ALLOWED`、`PROMPT_NOT_FOUND`、`PROMPT_VARIABLE_MISSING`、`REQUEST_VALIDATION_ERROR`。

日志记录 request_id、路径、状态、耗时、模型和 usage。`RedactingFilter` 处理 Key 和 Bearer。不记录消息正文。

## 8. 测试基线

命令：

```powershell
cd qs-test\qs_llm_dp
.\.venv\Scripts\python.exe -m pytest -q
```

2026-11-06 结果：

```text
57 passed, 4 skipped, 0 failed in 0.86s
```

跳过的 4 项是 `tests/test_live_deepseek.py`。只有 `DEEPSEEK_LIVE=1` 且环境变量中有 `DEEPSEEK_API_KEY` 才会请求真实 DeepSeek。本次 M0 没有重跑真实联调。上一轮真实联调记录是 `4 passed in 4.11s`，覆盖非流式、SSE、`23*19` 工具闭环和短超时；该次工具请求显式 `thinking.type=disabled`。

测试用 `FakeProvider` 和 `ScriptedClient`，不访问网络。

## 9. 已有能力

- 一个进程内复用的 AsyncOpenAI 客户端，关闭时 `aclose`。
- 非流式和 SSE Chat Completions。
- 服务端提示词和变量校验。
- 两个低风险工具的注册、参数校验、执行、轮次上限和合法 JSON 截断。
- 思考模式字段可下发；工具循环会在有值时回传 `reasoning_content`。
- 统一错误、request_id、密钥过滤和日志边界。
- 离线测试与可选真实联调。

## 10. 相对 Phase 2 的限制

这些是现有代码的边界，不是缺陷清单，M0 不修改它们。

- 没有 Agent 任务、步骤预算、墙钟 deadline、上下文预算或事件队列。
- 工具失败会中断整个 HTTP 请求，不会变成单次工具失败后继续让模型处理。
- 没有取消传播到工具线程的设计。`to_thread` 不能可靠取消已经运行的同步函数。
- 流式解析不能作为工具循环的模型接口。
- `ToolService.execute` 和解析器都把一次响应中的 tool call 上限写成 5。超过 5 个时，解析阶段就抛 `INVALID_MODEL_RESPONSE`，轮询逻辑看不到这些调用。
- 请求不能携带多轮内部工具历史。Phase 1 客户端也不能提交 `tool` 消息。
- 任务状态、队列和跨进程恢复都不存在。
- 没有按次覆盖 Provider 超时的参数。Agent deadline 若直接复用 `create_chat_completion`，仍会继承 Provider 的超时重试。
