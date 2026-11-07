# Cursor Start Prompt — Phase 2

请严格按照当前目录中的 `SKILL.md` 执行 DeepSeek FastAPI Phase 2。

项目根目录：包含 `app/`、`tests/`、`docs/phase2/` 和 `phase-skills/deepseek-fastapi-phase2-skill/` 的当前工作区。所有相对路径和命令都从该根目录解析。不要使用已废弃的 `qs-test/qs_llm_dp` 路径，也不要切换到旧目录。测试命令：`.\.venv\Scripts\python.exe -m pytest -q`。

第一阶段已冻结为 v0.1.0，严禁破坏任何既有 API、错误码、SSE 事件契约、内置工具和测试行为。

本次先不要直接写代码。请先：
1. 检查完整项目结构、README、依赖、配置、Provider、ChatService、工具模块、异常处理和测试。
2. 运行现有测试并记录精确结果。
3. 阅读 `references/` 下全部阶段规范。
4. 结合真实代码，输出 M0 基线报告和逐文件实施计划。
5. 标明哪些 Phase 1 组件可以复用，哪些需要新增适配层。
6. 指出本 Skill 中任何与现有代码不匹配的地方，并提出兼容调整。

完成 M0 后暂停，等待我确认，再开始 M1。不得一次性生成整个 Phase 2。
