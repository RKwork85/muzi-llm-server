# DeepSeek FastAPI Phase 2 Skill Pack

用于指导 Cursor 在当前项目根目录中实现 Phase 2：Harness / Agent Runtime。项目根目录是包含 `app/`、`tests/` 和 `phase-skills/deepseek-fastapi-phase2-skill/` 的目录。

## 内容

```text
deepseek-fastapi-phase2-skill/
├── SKILL.md
├── README.md
├── references/
│   ├── 01_PHASE2_ARCHITECTURE.md
│   ├── 02_RUNTIME_CONTRACTS.md
│   ├── 03_SECURITY_AND_FAILURES.md
│   ├── 04_TEST_AND_ACCEPTANCE.md
│   └── 05_OFFICIAL_REFERENCES.md
├── templates/
│   ├── CURSOR_START_PROMPT.md
│   └── CURSOR_CONTINUE_PROMPT.md
└── checklists/
    └── PHASE2_REVIEW.md
```

## 使用方式

1. 本 Skill 位于项目根目录下的 `phase-skills/deepseek-fastapi-phase2-skill/`。
2. 在 Cursor 中打开项目根目录。所有命令以该目录为工作目录，不要使用已废弃的 `qs-test/qs_llm_dp` 路径。
3. 将 `templates/CURSOR_START_PROMPT.md` 内容发给 Cursor。
4. 先完成 M0，只做代码审查、基线测试和实施计划。
5. 人工确认后逐个执行 M1 到 M7。
6. 每个里程碑必须通过测试，才允许进入下一个里程碑。

## 设计边界

- Phase 1 `v0.1.0` 是冻结基线。
- Phase 2 只新增 Agent Runtime，不重写 Provider。
- 本阶段采用轻量自研 Runtime，不引入 LangChain/LangGraph。
- 本阶段不引入数据库、Redis、RQ、Celery、MCP、RAG、多智能体或前端。
- 任务状态只在当前进程内有效，不能宣称持久化或跨 Worker 共享。
- 真实 DeepSeek 测试必须 opt-in；默认测试使用 mock。

## 重要说明

该 Skill 是工程规格和 Cursor 执行约束，不是已实现代码，也不保证 Cursor 自动正确。必须按里程碑审查代码、测试结果和安全边界。
