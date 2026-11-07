# Phase 3 基线

核对日期：2026-11-06。项目根目录 `C:\Users\rkwork\Desktop\muzihhh\work\muzi_project\qs_llm_dp`。

## Git

- 分支：`main`
- HEAD：`2c5d97f` `feat: implement Phase 2 M3 Agent Runtime`
- 前序：`b3c76d7` 基线记录、`995a207` M3 检查点、`1f1751b` M2、`a80a781` M1、`9b609ba` Skill 路径
- `git diff --stat` 为空，已跟踪文件没有未提交修改
- 未跟踪且必须保留：`phase-skills.zip`、`phase-skills/skill3_deepseek_fastapi_agent_runtime_plus/`
- 标签 `v0.1.0-phase1-baseline` 未移动

## 测试

```text
.\.venv\Scripts\python.exe -m pytest -q --tb=line
152 passed, 6 skipped in 5.65s
```

失败数为 0。6 个跳过全部来自 `tests/test_live_deepseek.py`，条件是 `DEEPSEEK_LIVE != 1`。本次没有打开该开关。

## 真实代码位置

| Skill 中的概念 | 真实位置 |
|---|---|
| ToolRegistry / ToolSpec | `app/tools/registry.py` |
| 参数 schema | `app/tools/schemas.py` 的 `parameters_schema` |
| 现有工具 | `app/tools/calculator.py`、`app/tools/clock.py` |
| ExecutionPolicy / RunClock | `app/agents/policy.py` |
| ToolExecutor | `app/agents/executor.py` |
| AgentLoop | `app/agents/loop.py` |
| Provider 适配 | `app/agents/provider_adapter.py` 的 `complete_for_agent` |
| DeepSeekProvider | `app/providers/deepseek.py`，base URL 来自 `Settings.deepseek_base_url` |
| Agent API | `app/api/v1/endpoints/agents.py` |
| 请求模型 | `app/agents/models.py` 的 `AgentRequest` |
| 响应模型 | `app/schemas/agents.py` 的 `AgentTaskResponse` |
| EventEnvelope | `app/agents/models.py`；发布与字段白名单在 `app/agents/events.py` |
| Settings | `app/core/config.py` |
| 错误码 | `app/core/exceptions.py` |

没有 `app/agents/tools/`。工具注册只有 `ToolRegistry` 这一套。

## 与后续实现直接相关的事实

- `ToolSpec` 字段只有 `name`、`description`、`args_model`、`handler`、`risk_level="low"`。`register` 拒绝任何非 `low`。
- `ToolExecutor`、`AgentLoop._allowed_schemas`、`low_risk_tool_names` 都把 `risk_level == "low"` 当作可执行条件。
- 默认策略的 `allowed_tools` 是全部 low 工具。当前只有 `calculator` 和 `get_current_time`。
- `AgentRequest` 已有收紧用的 `allowed_tools`。没有 `toolsets`。
- `AgentTaskResponse` 字段是 `task_id`、`request_id`、`status`、`answer`、`steps`、`tool_calls`、`error_code`、`usage`。没有 `artifacts`。
- 成功的工具结果被包成 `{"source":"server_tool","name","result"}`，`result` 是字符串。
- 同步 handler 走运行时注入的线程池；协程 handler 直接 `await`，不进线程池。池大小来自 `AGENT_SYNC_TOOL_CONCURRENCY`，默认 2。
- 事件 `data` 只保留 `step`、`tool_name`、`status`、`error_code`、`finish_reason`、`dropped_events`、`category`。列表会被丢掉。
- `httpx` 已在 `requirements.txt` 中直接声明。
- 总 Deadline 用 `RunClock.tool_timeout()`，取消会重新抛出 `CancelledError`。

## 官方文档

已重新读取：

- https://api-docs.deepseek.com/zh-cn/guides/search
- https://api-docs.deepseek.com/zh-cn/api/search
- https://api-docs.deepseek.com/zh-cn/api/fetch
- https://api-docs.deepseek.com/zh-cn/guides/tool_calls

Search / Fetch 的路径、字段、状态码、计费和限速与 `references/04_DEEPSEEK_SEARCH_FETCH_CONTRACT.md` 一致，没有发现需要改契约的字段差异。

Tool Calls 页面的示例函数名是 `get_weather` 这种 snake_case。该页没有另给 function name 的长度正则。`strict` 模式是 Beta，且不支持 `minLength`、`maxLength`、`minItems`、`maxItems`。本项目当前不启用 `strict`，长度限制继续由服务端执行。注册表已有名称规则 `^[A-Za-z0-9_-]{1,64}$`。
