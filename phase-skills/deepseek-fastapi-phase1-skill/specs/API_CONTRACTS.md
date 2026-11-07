# API 合约建议

所有接口建议使用 `/api/v1` 前缀；如果已有项目有既定前缀，遵循现有规范。

## 1. POST /api/v1/chat/completions

请求示例：

```json
{
  "messages": [
    {"role": "user", "content": "解释一下 FastAPI 的依赖注入"}
  ],
  "prompt_name": "default",
  "model": null,
  "temperature": 0.7,
  "max_tokens": 2048,
  "stream": false
}
```

注意：
- messages 至少有一条。
- role 只允许 system/developer/user/assistant/tool 中当前接口确实支持的角色；客户端不应任意注入 tool 消息来伪造服务端工具执行结果。
- system Prompt 建议由服务端 Prompt Registry 控制；若允许客户端传 system，必须明确权限策略。
- model 如果允许覆盖，必须检查配置 allowlist。
- 对参数范围做 Pydantic 校验。

响应建议包含：
- `request_id`
- `id`
- `model`
- `choices`
- `usage`
- `finish_reason`

## 2. POST /api/v1/chat/completions/stream

请求体与非流式尽量共用 Schema，stream 字段可省略或固定 true。

SSE 格式示例：

```text
event: delta
data: {"request_id":"...","content":"你好"}

event: done
data: {"request_id":"..."}
```

错误事件：

```text
event: error
data: {"request_id":"...","error":{"code":"UPSTREAM_TIMEOUT","message":"模型服务请求超时"}}
```

必须设置正确的 `text/event-stream` media type。客户端断开后应取消上游流并清理连接。

## 3. POST /api/v1/chat/tool-calls

请求示例：

```json
{
  "messages": [
    {"role": "user", "content": "帮我计算 23 * 19"}
  ],
  "prompt_name": "default",
  "max_tool_rounds": 3
}
```

服务端工具定义不由客户端直接传入 Python handler。服务端注册 calculator 等工具并把工具 schema 发给模型。

执行闭环：
1. 请求模型。
2. 若返回 tool_calls，解析 arguments。
3. 检查工具名 allowlist。
4. 对参数进行 JSON 和 Pydantic 验证。
5. 执行安全 handler。
6. 将 assistant tool_calls message 和 tool result message 加入 messages。
7. 再次请求模型。
8. 没有 tool_calls 时返回最终答案。
9. 达到轮数上限时返回 TOOL_ROUND_LIMIT 或受控结果。

## 4. GET /api/v1/models

返回服务端配置允许的模型列表，例如：

```json
{
  "data": [
    {"id": "deepseek-flash", "provider": "deepseek"}
  ]
}
```

模型 ID 只是示例，运行时以环境配置和 DeepSeek 官方更新日志为准。

## 5. GET /health

返回服务进程健康状态，不调用上游模型。

## 统一错误格式

```json
{
  "error": {
    "code": "UPSTREAM_RATE_LIMIT",
    "message": "模型服务请求过于频繁，请稍后重试",
    "request_id": "..."
  }
}
```

不要把上游原始响应、API Key、内部堆栈返回给客户端。
