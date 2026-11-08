# Chat / Agent 集成

调用链：

- Chat：`app/api/v1/endpoints/chat.py` 的流式接口解析已登录用户，再进入 `ChatService.prepare_stream()`。原有系统提示和 `skill_notes()` 先拼好，然后 `_apply_registered_skills()` 调用 `SkillPromptAdapter`。
- Agent：`AgentRuntime.start()` 沿用现有任务号和 `mcp_user_id`，在创建循环前调用同一个适配器。`AgentLoop`、`ToolExecutor` 和 MCP 工具装配没有改写。
- 没有 `skill_mode` 时，两条路径都不查询注册表。

`skills_root` 为空时，`app/main.py` 不挂运行时，现有对话保持原样。配置了目录之后，Chat 和 Agent 共用一个 `SkillOrchestrator`。

旧四条提示仍只在 Plan 和 Agent 的 `skill_names` 上生效。注册表技能要另设 `skill_mode`。同名不会互相覆盖。

测试 `tests/skills/test_skill_integration.py` 确认：

- 未开启模式时，正文不进入系统提示。
- 开启 auto 后，系统提示仍以原提示词开头，后面才是不可信技能段落。
- Agent 收到的提示词同样以计算器提示词开头，并包含同一边界声明。
- 两个入口的 `skill_runtime` 是同一个对象。

没有改 `app/mcp/`、`app/tools/code_runner.py`、`app/tools/sandbox_command.py` 和前端插件。
