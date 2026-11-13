# 复用矩阵

| 实施说明中的能力 | 决定 | 真实位置 |
|---|---|---|
| LLM Provider | 复用 | `app/providers/deepseek.py`、`app/providers/manager.py`、`app/agents/provider_adapter.py` |
| 对话推理 | 复用 | `app/services/chat_service.py` |
| Agent Runtime | 复用，不新建循环 | `app/agents/runtime.py` |
| Agent Loop | 复用 | `app/agents/loop.py` |
| Tool Registry | 复用 | `app/tools/registry.py` |
| Tool Executor | 复用 | `app/agents/executor.py`、`app/services/tool_service.py` |
| ExecutionPolicy | 复用 | `app/agents/policy.py` |
| 用户认证 | 复用 | `app/services/auth_service.py` |
| Prompt 装载方式 | 复用风格 | `app/services/prompt_service.py`、`app/tools/prompts.py` |
| 会话消息 | 复用表 | `sessions`、`messages` |
| Context Manager | 复用，增加注入点 | `app/agents/context.py`；对话侧系统提示在 `ChatService._mode_prompt()` |
| Task Manager | 复用 | `app/agents/task_manager.py`，表 `agent_tasks` |
| 文件隔离 | 复用 | `app/tools/file_sandbox.py` |
| SSE 编码 | 复用 | `app/core/sse.py`、`app/agents/events.py` |
| 数据库和迁移 | 复用 | `app/core/database/`、`alembic/versions/` |
| 写死的四条技能提示 | 不扩展成系统本体 | `app/prompts/skills.py`。新系统就绪后，对话改为读取已启用 Skill 的元数据 |
| 代码进程 | 不冒充 Skill 沙箱 | `app/tools/code_runner.py` 只服务 `run_python` |
| 组织权限、项目权限 | 现有代码没有对应实体 | 见 DECISIONS.md |
| Skill Registry、Discovery、Loader、Runtime、Security、Persistence | 新增 | 计划放在 `app/skills/`，不放进 `app/tools/` |
