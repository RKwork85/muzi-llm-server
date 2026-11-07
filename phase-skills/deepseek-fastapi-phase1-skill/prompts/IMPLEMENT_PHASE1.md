# Cursor 实施任务：DeepSeek FastAPI 第一阶段

你是资深 Python 后端工程师。请先阅读：

1. `.cursor/skills/deepseek-fastapi-phase1/SKILL.md`
2. `.cursor/skills/deepseek-fastapi-phase1/references/official-docs.md`
3. `specs/PHASE1_SCOPE.md`
4. `specs/API_CONTRACTS.md`
5. `specs/ACCEPTANCE_CRITERIA.md`

## 任务

在当前仓库中完成 DeepSeek FastAPI 第一阶段。第一步必须检查当前项目，不要直接生成一套与现有仓库冲突的新项目。

## 执行步骤

### Step 1：仓库审查
- 检查 Python 版本、依赖文件、FastAPI 入口、现有目录结构、配置方式、日志、测试。
- 判断是否已有 LLM Provider、API Router、异常处理等可复用实现。
- 输出差异分析和实施计划。
- 只有在发现会影响架构的重大歧义时才询问；其他情况采用最小侵入方案。

### Step 2：确认官方 API
- 打开 DeepSeek 官方 Chat Completions、Tool Calls、Streaming、JSON Output、Thinking Mode 和 Updates 文档。
- 核对当前模型 ID 和参数；将模型 ID 放入配置，不得硬编码。
- 以 Chat Completions 作为本阶段主协议；Responses API 仅留作后续扩展点。

### Step 3：实现基础服务
- 配置层、Async Provider、Chat Service、Prompt Registry。
- 非流式接口和 SSE 接口。
- 生命周期中创建/关闭 AsyncOpenAI 客户端。
- 统一 request_id 和异常映射。

### Step 4：实现 Function Calling
- 创建服务端 Tool Registry。
- 至少实现 calculator 安全示例工具。
- 将工具 schema 传给 DeepSeek。
- 验证 tool_calls 名称与参数。
- 执行 handler 并将 tool result 回传模型。
- 限制最大轮数、工具超时和结果大小。
- 禁止 eval/exec、任意 shell 和任意文件系统访问。

### Step 5：测试
- 使用 mock 测试，不依赖真实 DeepSeek Key。
- 覆盖验收清单中的关键路径。
- 修复所有测试失败。
- 如环境不允许安装依赖或执行测试，必须如实说明，不得伪造通过结果。

### Step 6：文档与交付
- 更新 requirements.txt 或 pyproject.toml（沿用仓库现有依赖管理方式）。
- 提供 `.env.example`。
- 更新 README。
- 给出启动命令、PowerShell 示例、curl 示例、Swagger 地址。
- 报告完成内容、文件变更、测试结果、未完成项和风险。

## 工程约束
- 使用类型标注和 Pydantic Schema。
- Endpoint 只负责 HTTP 层，不直接拼接 SDK 请求。
- Provider 负责模型 SDK 适配；Service 负责业务编排。
- 不要把所有逻辑堆在一个文件。
- 不要为了抽象而过度设计。
- 保留项目原有注释、命名和已有功能。
- 不要修改与任务无关的文件。
- 不得提交真实 API Key。
- 不要把模型输出当作可信指令。
- 不要把第一阶段扩展成 Harness Agent 项目。

## 最终回复格式
1. 已完成的功能
2. 核心架构和调用链路
3. 新增/修改文件
4. 安装与启动命令
5. API 调用示例
6. 测试命令及真实测试结果
7. 安全边界
8. 暂未实现的第二阶段内容
