# DeepSeek FastAPI 第一阶段开发 Skill

本压缩包用于让 Cursor 按照明确的工程规范，在现有项目或新项目中实现第一阶段 DeepSeek AI 后端服务。

## 第一阶段目标

交付一个可运行、可测试、可扩展的 FastAPI 服务，支持：

1. DeepSeek 模型配置与 Provider 封装
2. 非流式 Chat Completions
3. SSE 流式对话
4. Prompt 模板/系统提示词管理
5. 多轮对话（第一阶段采用客户端传入 messages；可选轻量内存会话，不强制引入数据库）
6. Function Calling：工具注册、参数校验、执行、结果回传模型
7. 统一错误处理、超时、日志、配置校验
8. pytest 自动化测试与 OpenAPI 文档

本阶段**不实现** Harness、Agent 自主规划、多 Agent、MCP、RAG、向量数据库、长期记忆、Redis/RQ、前端 UI、用户认证体系。这些留到后续阶段，避免第一阶段过度设计。

## 安装到 Cursor

将压缩包解压到项目根目录，保留 `.cursor/skills/deepseek-fastapi-phase1/SKILL.md` 结构。

然后在 Cursor Agent 中输入：

> 请先完整阅读 `.cursor/skills/deepseek-fastapi-phase1/SKILL.md` 及其 references，再检查当前仓库。严格按 `prompts/IMPLEMENT_PHASE1.md` 和 `specs/` 实施第一阶段。先给出仓库现状与实施计划，再按阶段修改文件；不要覆盖无关代码；完成后运行测试并报告结果。

如果希望 Cursor 自动遵循仓库级约束，可将 `AGENTS.md` 复制到项目根目录（本压缩包提供模板）。

## 建议技术栈

- Python 3.11+（项目若已固定 Python 3.13，优先沿用现有版本并验证依赖兼容）
- FastAPI
- Uvicorn
- OpenAI Python SDK（使用 DeepSeek 的 OpenAI-compatible Chat Completions endpoint）
- Pydantic v2 / pydantic-settings
- httpx（测试）
- pytest
- Ruff（格式与 lint，可选但推荐）

## 重要说明

- 当前模型 ID、API 能力可能变化。默认模型只作为配置示例，必须以 DeepSeek 官方模型列表和更新日志为准。
- 本包资料链接于 2026-11-06 核验。Cursor 实施时应优先检查官方文档，不得把旧模型名写死在业务代码中。
- 不要把 DeepSeek Harness SDK 当作第一阶段的必要依赖。
- Function Calling 不是模型直接执行函数：后端必须自行校验并执行工具，然后把 tool 结果回传模型。
