# 第一阶段范围与最终效果

## 产品目标

建设独立的 FastAPI LLM 基础服务，为未来 Agent/Harness、内容生成、数据采集和其他业务系统提供统一模型能力。

## 必须交付的能力

| 能力 | 对外表现 | 内部实现 |
|---|---|---|
| 模型接入 | 调用 DeepSeek | Async Provider |
| 对话 | 普通问答、多轮上下文 | Chat Service |
| 流式 | 实时返回文本增量 | SSE |
| Prompt | 服务端选择系统角色/模板 | Prompt Registry |
| 工具调用 | 模型请求调用白名单工具 | Tool Registry + Tool Executor |
| 结构化输出 | JSON 模式响应 | response_format + Pydantic 校验 |
| 错误处理 | 稳定错误码和 request_id | Exception Mapper |
| 可测试 | 无 Key 也可跑测试 | Mock Provider + pytest |
| 可维护 | 可切换模型、替换 Provider | Adapter Interface |

## 明确不包含

Harness、Agent 自主规划、多 Agent、MCP、RAG、Embedding、向量数据库、长期记忆、Redis/RQ、数据库持久化、复杂鉴权和前端。

## 完成后用户能做什么

1. 通过一个 HTTP 接口调用 DeepSeek。
2. 用 messages 传入上下文，完成多轮对话。
3. 通过 SSE 逐段接收模型输出。
4. 选择服务端预设 Prompt。
5. 让模型提出调用 calculator 等安全工具的请求，由服务端验证并执行。
6. 获取工具执行后的模型总结。
7. 在 Swagger 中调试 API。
8. 后续可将 Provider 扩展到其他模型，或在第二阶段接入 Harness，而不重写 API 层。

## 非目标

本阶段不是完整 Agent。有限的 Function Calling 往返不等于自主规划、长期任务管理或多智能体。
