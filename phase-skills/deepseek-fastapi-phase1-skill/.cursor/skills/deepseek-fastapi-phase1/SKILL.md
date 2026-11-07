---
name: deepseek-fastapi-phase1
description: 在 FastAPI 项目中实现 DeepSeek 第一阶段模型服务，包括 Provider、Chat Completions、SSE、Prompt、Function Calling、安全校验、错误处理和测试。用户要求接入 DeepSeek、实现基础 LLM 后端或第一阶段 AI 服务时使用。
---

# DeepSeek FastAPI Phase 1 Skill

## 0. 工作方式（必须遵守）

1. 先检查仓库：目录结构、Python 版本、依赖管理方式、现有 FastAPI app、配置、日志、测试、代码风格。
2. 如果已有项目，优先最小侵入式集成；不得擅自重建项目、删除文件、替换已有架构或修改无关模块。
3. 在开始编码前，输出简短的现状分析、目标目录结构、实施步骤和需要用户确认的阻塞项。
4. 除非用户明确要求，不要在第一阶段引入 Harness、数据库、Redis、RQ、MCP、RAG 或 Multi-Agent。
5. 以 DeepSeek 官方文档为事实来源。模型 ID、API 参数和接口行为必须可配置，不得散落硬编码。
6. 必须完成测试。没有真实 API Key 时，使用 mock 测试，不得声称真实 API 已验证。
7. 输出完整文件路径、启动命令、环境变量说明、接口示例、测试结果和已知限制。

## 1. 第一阶段交付范围

### 必须实现
- Settings：`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`、超时、重试、日志级别。
- Provider/Client：封装 OpenAI SDK 的 Async 客户端，统一 DeepSeek 请求入口。
- Chat：非流式 Chat Completions。
- Streaming：SSE 流式输出，正确处理 chunk、结束标记、客户端断开和异常。
- Prompt：系统提示词和模板的集中管理；避免把业务 Prompt 写在 endpoint 中。
- Tool Registry：工具 schema 注册、函数映射、参数验证、执行结果封装。
- Tool Calling Loop：识别 tool_calls，执行允许的工具，将 tool role 结果加入 messages 后再次请求模型；限制最大轮数。
- Error Handling：配置错误、上游超时、认证失败、限流、上游错误、无效响应等统一处理。
- Tests：Provider mock、Chat endpoint、SSE、Prompt、工具调用成功/失败/非法参数、最大轮数、错误映射。
- API 文档：请求/响应 Schema、错误格式、Swagger/OpenAPI。

### 可选实现
- 简单的内存会话存储，仅用于本地演示；必须明确进程重启会丢失，不能宣称生产持久化。
- `/health` 和 `/ready`。
- Token usage 记录（如果上游返回 usage）。

### 明确不做
- DeepSeek Harness SDK / Agent Runtime
- 自动任务规划、自主循环（除受限的 Function Calling 往返）
- MCP Server/Client
- Web Search、Firecrawl
- RAG、Embedding、Vector DB
- 长期 Memory
- Multi-Agent/Subagent
- Redis、RQ、Celery
- 用户注册、登录、计费、复杂 RBAC
- 前端界面

## 2. 推荐架构

按项目现状调整，但职责必须清晰：

```text
app/
  main.py
  api/
    v1/
      endpoints/
        chat.py
        models.py          # 若实现模型能力查询
  core/
    config.py
    logging.py
    exceptions.py
  schemas/
    chat.py
    common.py
  services/
    chat_service.py
    prompt_service.py
    tool_service.py
  providers/
    base.py
    deepseek.py
    manager.py
  prompts/
    system/
      default.txt
  tools/
    registry.py
    schemas.py
  tests/
```

不要为了符合示例目录而破坏已有目录约定。关键是 endpoint 不直接持有 SDK 细节，业务服务不依赖 FastAPI Request 对象，Provider 不包含业务 Prompt。

## 3. API 设计建议

建议统一前缀 `/api/v1`。若项目已有版本前缀，沿用现有约定。

### `POST /api/v1/chat/completions`
非流式对话。请求包含：
- `messages`: role/content 消息列表
- `model`: 可选；默认使用配置模型。允许覆盖时必须做 allowlist 校验
- `temperature`: 可选，范围按官方 API 文档
- `max_tokens`: 可选，正整数
- `thinking`: 可选，按当前官方 API schema
- `response_format`: 可选
- `tools`: 可选工具列表或由服务端注册表注入；不要无条件信任客户端传入可执行函数
- `tool_choice`: 可选

响应应统一返回：
- `id`
- `model`
- `message` 或 `choices`
- `finish_reason`
- `usage`（上游存在时）
- `request_id`（本服务追踪 ID）

### `POST /api/v1/chat/completions/stream`
SSE 流式对话。建议事件：
- `event: delta`：增量文本/思考内容（仅在产品明确允许暴露时）
- `event: usage`：usage 信息
- `event: error`：统一错误数据
- `event: done`：结束

每条 data 都是 JSON。结束时发送 `event: done`。客户端断开时及时取消上游请求并释放资源。不得把 API Key、内部堆栈或完整敏感请求写入 SSE。

### `POST /api/v1/chat/tool-calls`
或者在 completions 服务内部提供 `execute_tools=true` 的受控模式。选择一种一致方案，不要两种逻辑重复实现。
- 模型返回 tool_calls
- 服务端检查工具名是否在 allowlist
- JSON parse + Pydantic/JSON Schema 校验参数
- 执行对应 Python 函数
- 将 tool result 以 `role=tool`、`tool_call_id` 加入 messages
- 再次调用模型
- 达到最大轮数则停止并返回可诊断状态

工具调用必须设置 `max_tool_rounds`，默认建议 3，硬上限建议 5。对危险工具不做默认注册。

### `GET /api/v1/models`
返回本服务允许调用的模型列表（从配置 allowlist 来），不承诺等于上游实时全部模型。

### `GET /health`
进程存活检查，不调用 DeepSeek。

## 4. Provider 规则

- 使用 `openai.AsyncOpenAI(api_key=..., base_url=...)` 或经官方文档验证的兼容客户端。
- 默认 DeepSeek Chat Completions base URL 配置为 `https://api.deepseek.com`；不要把 `/chat/completions` 重复拼进 base_url（SDK 会处理资源路径）。
- 默认模型值从环境变量读取；截至资料核验日，官方更新日志列出 `deepseek-flash` 为当前 Flash API model ID。必须保留可配置性并提示用户按官方更新日志核验。
- Provider 暴露窄接口，例如 `create_chat_completion(...)`、`stream_chat_completion(...)`。
- 使用 Async 客户端，应用 lifespan 中创建并关闭客户端；不要每个请求重复创建连接池。
- 配置初始化时校验 API Key；开发环境允许启动但请求时明确报错也可以，但行为要一致。
- 设置合理 timeout；重试仅对可重试错误进行，避免对非幂等工具执行链路盲目重试。
- 不要记录 Authorization header、API Key、完整敏感 prompt 或用户私密数据。

## 5. Function Calling 安全要求

模型输出的 function arguments 是不可信输入。必须：
- 只执行服务端注册的工具；不能 `eval`、`exec` 模型返回的代码。
- 解析 JSON，校验字段、类型、必填项、长度、数值范围。
- 拒绝未知字段或按明确策略处理。
- 工具参数通过 Pydantic 模型验证。
- 对每个工具定义 `name`、`description`、`parameters`、`handler`、`risk_level`。
- 设置执行超时、结果大小限制和最大工具轮数。
- 默认不提供任意文件读写、Shell、网络代理、系统命令工具。
- 如果后续增加高风险工具，必须增加权限检查和人工审批；第一阶段不实现危险工具。
- Tool output 不可信，回传模型前应限制大小并标明来源。
- 避免工具递归调用或无限循环。

## 6. Prompt 管理

- System Prompt 默认由服务端控制，不能让普通客户端通过传入 `system` message 无条件覆盖。
- 支持 Prompt 名称/版本或集中模板文件。
- Prompt 参数使用显式变量，渲染时校验缺失变量。
- 不把 API Key、内部提示词、服务端路径、其他用户会话信息注入 Prompt。
- 对 Prompt Injection 不宣称能完全防御；工具权限必须由后端代码执行层控制，而不是依赖 Prompt。

## 7. 错误处理

统一错误结构建议：

```json
{
  "error": {
    "code": "UPSTREAM_TIMEOUT",
    "message": "模型服务请求超时",
    "request_id": "..."
  }
}
```

至少覆盖：
- `CONFIGURATION_ERROR`
- `UPSTREAM_AUTH_ERROR`
- `UPSTREAM_RATE_LIMIT`
- `UPSTREAM_TIMEOUT`
- `UPSTREAM_ERROR`
- `INVALID_MODEL_RESPONSE`
- `INVALID_TOOL_ARGUMENTS`
- `TOOL_NOT_ALLOWED`
- `TOOL_EXECUTION_ERROR`
- `TOOL_ROUND_LIMIT`
- `INTERNAL_ERROR`

不要将上游原始错误全文直接返回客户端；日志可保留脱敏后的诊断字段。

## 8. 测试要求

必须使用 pytest。对外部 DeepSeek API 默认 mock，不要求 CI 使用真实 API Key。

最低测试：
1. Settings 缺少/存在 API Key 的行为。
2. Chat 正常请求与响应映射。
3. 上游 401、429、超时、5xx 的错误映射。
4. SSE 正常 delta、结束、异常。
5. Prompt 模板变量渲染与缺失变量。
6. 注册工具可调用。
7. 未注册工具拒绝执行。
8. 非法 JSON 参数拒绝执行。
9. 参数超范围拒绝执行。
10. 工具结果回传后模型完成最终回答。
11. 达到最大工具轮数停止。
12. 不泄漏密钥到响应和日志（可用 caplog 验证）。

## 9. 交付验收

必须提供：
- 可启动项目
- `.env.example`（不得放真实密钥）
- requirements/pyproject 更新
- Swagger 页面可访问
- README：安装、配置、启动、接口 curl 示例
- 自动化测试结果
- 一个安全的示例工具（例如 `get_current_time` 或 `calculator`，不访问系统资源）
- 明确列出未实现内容和后续阶段建议

## 10. 官方资料优先级

参阅 `references/official-docs.md`。如果文档与本 Skill 冲突，以当前 DeepSeek 官方 API 文档为准；以 FastAPI 官方文档为准处理生命周期、依赖注入、测试和 StreamingResponse。
