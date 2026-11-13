# M3 / M4 变更

## 新增

- `app/skills/discovery.py`、`discovery_models.py`、`loader.py`
- `app/skills/runtime_state.py`、`activation_store.py`、`context_builder.py`、`resources.py`
- `app/skills/orchestrator.py`、`adapters.py`、`legacy.py`
- `tests/skills/test_discovery.py`、`test_runtime_skills.py`、`test_skill_integration.py`

## 修改

- `app/skills/models.py`：元数据增加标签。
- `app/skills/paths.py`：允许 `templates/`。
- `app/skills/registry.py`：按版本读取摘要和包目录，标签来自索引。
- `app/services/chat_service.py`、`app/agents/runtime.py`、`app/main.py`：挂上同一个适配器。
- `app/api/v1/endpoints/chat.py`：流式对话在各模式下都能拿到已登录用户，远程工具仍只在 Agent 模式加载。
- `app/schemas/chat.py`、`app/agents/models.py`：可选 `skill_mode` 和 `explicit_skill_ids`。
- `app/core/config.py`：`skills_root`，默认为空。
- `app/core/exceptions.py`：`SkillFlowError`。

## 没有改动

- `app/prompts/skills.py`
- `app/mcp/`
- `app/tools/code_runner.py`
- `app/tools/sandbox_command.py`
- `docs/phase3/`
- `tests/database/test_metadata.py`
- 数据库迁移

没有创建 Git 仓库或提交。
