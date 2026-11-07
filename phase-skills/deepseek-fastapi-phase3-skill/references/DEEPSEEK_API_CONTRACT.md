# DeepSeek API 官方协议参考

核对日期：2026-11-06。官方文档可能变化，实施时应再次打开来源确认。

## 1. 接入和模型

- 官方 OpenAI 兼容 Base URL：`https://api.deepseek.com`
- Chat Completions：`POST /chat/completions`
- 官方首次调用页当前示例模型：`deepseek-flash`；实际可用模型以官方模型页和账户权限为准。
- Chat Completions 多轮对话由客户端在 `messages` 中回传完整历史，不能假设 API 服务端保存会话。

来源：
- https://api-docs.deepseek.com/zh-cn/
- https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/

## 2. Tool Calls

- 模型返回工具调用意图，工具实际执行由应用负责。
- 标准历史顺序：assistant 消息（包含 tool_calls）→ 对应的 tool 消息（tool_call_id）→ 下一次模型请求。
- 每个工具结果必须关联正确 tool_call_id。
- Chat Completions 不支持在对话历史中间插入非模型生成的 tool call；需要这种能力时官方建议评估 Anthropic API 或 Responses API。
- `strict` 是 Beta 能力，需使用 `https://api.deepseek.com/beta`，并受支持 JSON Schema 子集约束；本项目默认不依赖它。

来源：
- https://api-docs.deepseek.com/zh-cn/guides/tool_calls/

## 3. Thinking Mode

- 官方文档说明，从 DeepSeek-V3.2 起支持思考模式下的工具调用。
- 带 tools 的思考模式请求，后续请求必须正确回传 assistant 的 `reasoning_content`，即使该轮未实际调用工具，否则可能返回 400。
- reasoning_content 不应作为用户可见内容或普通日志输出。
- 是否开启 thinking 必须沿用项目现有配置和当前模型能力，不可擅自默认启用。

来源：
- https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/

## 4. Streaming 与 finish_reason

- Chat Completions `stream=true` 使用 SSE，正常流以 `data: [DONE]` 结束。
- `stream_options` 必须与 `stream=true` 同时使用。
- `include_usage` 的 usage 可能出现在 `[DONE]` 前最后一个 chunk。
- 文档列出的 finish_reason 包括 `stop`、`length`、`content_filter`、`tool_calls`、`insufficient_system_resource`、`aborted`。应以当前 API schema 为准并安全处理未知值。

来源：
- https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/

## 5. 错误码

- 400：格式错误
- 401：认证失败
- 402：余额不足
- 422：参数错误
- 429：请求速率/并发达到上限
- 500：服务器故障
- 503：服务器繁忙

只有在总 Deadline、重试预算和幂等性约束内才允许有限重试。工具默认不自动重试。

来源：
- https://api-docs.deepseek.com/zh-cn/quick_start/error_codes/

## 6. 限速与用户隔离

官方限速可能按账号和模型变化；不要将文档中的静态并发数当成本地可无限并发额度。应用仍需设置自己的并发闸门。`user_id` 是可用于业务用户隔离的 API 参数，是否接入需先核实当前项目的隐私和用户标识策略。

来源：
- https://api-docs.deepseek.com/zh-cn/quick_start/rate_limit/

## 7. Responses API 的注意事项

官方 Responses API 是无状态接口，多轮对话需客户端回传完整 input。不要因为其名称较新就擅自迁移本项目；只有现有 Provider 架构或明确需求支持迁移，并完成兼容评估后才能采用。

来源：
- https://api-docs.deepseek.com/zh-cn/api/create-response/
- https://api-docs.deepseek.com/zh-cn/guides/responses_api/
