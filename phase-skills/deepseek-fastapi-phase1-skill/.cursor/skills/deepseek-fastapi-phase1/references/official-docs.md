# 官方资料索引（核验日期：2026-11-06）

> 本文件保存官方链接和实现时需要核对的重点，不复制第三方教程或整篇官方文档。API 参数、模型 ID 和能力可能变化，实施时重新核对链接。

## DeepSeek API

1. API 首页 / 首次调用  
   https://api-docs.deepseek.com/zh-cn/  
   重点：API Key、base URL、OpenAI-compatible 调用格式、当前示例模型 ID。

2. Chat Completions API  
   https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/  
   重点：messages、model、thinking、reasoning_effort、max_tokens、response_format、stream、tools、tool_choice、usage、finish_reason。

3. Tool Calls / Function Calling  
   https://api-docs.deepseek.com/zh-cn/guides/tool_calls/  
   重点：工具声明、模型返回 tool_calls、后端执行函数、tool result 回传、思考模式兼容性说明。模型不会替你的服务执行 Python 函数。

4. Streaming  
   https://api-docs.deepseek.com/zh-cn/guides/streaming/  
   重点：SSE chunk 结构、结束标志、流式 usage。

5. JSON Output  
   https://api-docs.deepseek.com/zh-cn/guides/json_mode/  
   重点：response_format={"type":"json_object"}，Prompt 必须明确要求 JSON，处理空内容和 length 截断。

6. Thinking Mode  
   https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/  
   重点：思考模式参数与模型支持情况。不要假设所有模型/参数组合都支持同样的工具调用选项。

7. API 更新日志  
   https://api-docs.deepseek.com/zh-cn/updates/  
   重点：模型名称、旧模型下线、参数行为变化。2026-10-17 更新记录中，当前 Flash API 名称为 `deepseek-flash`；必须配置化，不要散落硬编码。

8. Responses API（可选阅读，不作为第一阶段主实现）  
   https://api-docs.deepseek.com/zh-cn/guides/responses_api/  
   重点：DeepSeek 也支持 Responses API 格式；第一阶段为减少复杂度，优先选 Chat Completions，未来可增加独立 adapter。

## FastAPI

1. Dependencies / Dependency Injection  
   https://fastapi.tiangolo.com/tutorial/dependencies/

2. Testing  
   https://fastapi.tiangolo.com/tutorial/testing/

3. StreamingResponse  
   https://fastapi.tiangolo.com/advanced/custom-response/#streamingresponse

4. Lifespan Events  
   https://fastapi.tiangolo.com/advanced/events/

5. Settings and Environment Variables  
   https://fastapi.tiangolo.com/advanced/settings/

## SDK

- OpenAI Python SDK（DeepSeek OpenAI-compatible Chat Completions 使用方式，以 DeepSeek 官方文档为最终依据）：  
  https://github.com/openai/openai-python

- DeepSeek Harness（明确属于后续阶段，不是本阶段必需）：  
  https://deepseek-harness.github.io/deepseek-harness/guide/quickstart  
  https://deepseek-harness.github.io/deepseek-harness/guide/python-sdk

## 选型结论

- 第一阶段优先使用 DeepSeek Chat Completions + OpenAI Python SDK Async 客户端。
- DeepSeek Harness 留到第二阶段；不要因为 Harness SDK 存在就将第一阶段改成 Harness Runtime。
- Function Calling 必须由服务端实现安全执行闭环。
- 模型 ID、base URL、超时、重试和工具轮数全部配置化。
