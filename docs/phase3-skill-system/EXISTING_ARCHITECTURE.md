# 现有架构

核对对象：`muzi-llm-server/app`，2026-11-08。

## 请求怎么走到模型

对话入口是 `app/api/v1/endpoints/chat.py`，服务是 `app/services/chat_service.py`。Ask、Plan、Agent 三种对话模式由 `app/agents/toolsets.py` 的 `chat_mode_tool_names()` 选择工具名。Ask 使用 `ASK_TOOLS`，Plan 使用 `PLAN_TOOLS`，对话中的 Agent 复用 Ask 工具再附加已接入的 MCP。

独立 Agent 任务走 `app/api/v1/endpoints/agents.py` 和 `app/agents/runtime.py` 的 `AgentRuntime`。循环在 `app/agents/loop.py` 的 `AgentLoop`，只负责模型和工具的有限轮次，不直接处理 HTTP。

## 工具

`app/tools/registry.py` 的 `ToolRegistry.register()` 只接受 `risk_level="low"`，并且要求访问级别、幂等性和联网标记一致。执行有两条：

- 对话：`app/services/tool_service.py` 的 `ToolService`
- Agent 任务：`app/agents/executor.py` 的 `ToolExecutor`

两边都校验参数、超时和结果长度。策略在 `app/agents/policy.py` 的 `ExecutionPolicy` 和 `RunClock`。工具是否对 Ask 或 Agent 可见，由 `app/tools/capabilities.py` 决定。删除和 `run_command` 需要授权。`run_python` 在工作区可用时进入 Ask，实现在 `app/tools/code_runner.py`：独立进程、工作区目录、超时和输出截断。它没有 CPU、内存和网络隔离，不能当成第三方 Skill 脚本沙箱。

## 上下文、任务、事件

`app/agents/context.py` 的 `ContextManager` 按完整工具组裁剪消息，系统提示和用户消息在构造时传入。它没有 Skill 激活槽。

`app/agents/task_manager.py` 的 `TaskManager` 管内存中的 Agent 任务。表 `agent_tasks`、`executions`、`tool_calls`、`events` 在 `app/models/tables.py`。

`app/agents/events.py` 的 `EventPublisher.publish()` 只保留白名单字段：`step`、`tool_name`、`status`、`error_code`、`finish_reason`、`dropped_events`、`category` 以及若干 artifact 字段。对话流的 SSE 帧由 `app/core/sse.py` 的 `format_sse()` 编码。

## 用户和文件

用户表是 `users`。认证在 `app/services/auth_service.py` 和 `app/api/v1/endpoints/auth.py`。没有组织表，也没有项目成员表。

工作区文件通过 `app/tools/file_sandbox.py` 限制在配置的根目录内。任务数据在 `app/data/store.py`。下载和上传走 `app/api/v1/endpoints/files.py`、`app/tools/deliver_file.py` 和 `app/data/oss_client.py`。

## 数据库

Alembic 当前版本链：

1. `6de1140597d2_create_core_tables`
2. `b7e1c0a94d11_mcp_user_connections`
3. `c4a8e2b71f03_mcp_authorization_grants`

Skill 表还不存在。迁移必须接在 `c4a8e2b71f03` 之后。

## MCP

远程 MCP 在 `app/mcp/`，目录由 `connectors.toml` 维护，用户令牌按用户保存。它是 Tool 层的一种外部调用，不是 Skill。
