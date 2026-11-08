# Cursor Agent 总执行指令：Skill System M3 + M4

你现在负责在现有项目中实施 Skill System 的 M3 Discovery 与 M4 Runtime。两个阶段可以在一个交付周期内连续完成，但必须保留独立模块边界、独立测试和独立验收结论。

## A. 工作目录

真实后端目录：`muzi-llm/muzi-llm-server`。

首先确认 Cursor 当前工作区实际路径。不要使用 `muzi_project/muzi-llm-server`。不要假设 `muzi-llm` 或 `muzi-llm-server` 是 Git 仓库。

## B. 第一阶段：只读审查（禁止改代码）

完整阅读本执行包，并审查真实仓库：
1. `app/skills/` 全部现有模块。
2. `tests/skills/` 全部测试。
3. `docs/phase3-skill-system/` 现有基线。
4. `app/prompts/skills.py` 及四条旧提示的所有引用点。
5. Chat 请求、prompt builder、Provider 调用链。
6. Agent Runtime、任务状态、上下文构建和工具调用链。
7. 用户身份从认证层传递到服务层的真实路径。
8. 数据库模型、迁移方式、文件存储根目录和配置方式。
9. MCP、插件、代码执行相关文件只读识别并记录保护范围。

输出 `M3_M4_IMPLEMENTATION_AUDIT.md`，内容包括：
- 实际路径与入口。
- 已有类/函数/协议/状态机。
- Chat 和 Agent 真实调用链。
- 现有注册表能提供的查询接口。
- 已有激活模型是否可复用。
- 当前实现与本包规格的差距。
- 需要新增/修改的最小文件清单。
- 数据库变更必要性及理由。
- 测试基线和已知失败复核。
- 风险、冲突和建议实施顺序。

第一阶段不得修改任何源代码、测试、配置、数据库迁移或旧文档。

## C. 审查后暂停

审查报告完成后停止，不要自动进入编码。向用户报告：
- 哪些设计可直接复用。
- 哪些规格需要因真实代码调整。
- 计划修改哪些文件。
- 是否需要数据库迁移。
- 如何保护 MCP/插件/代码执行未提交改动。
- 预计测试方案。

等待用户确认实施方案。

## D. 第二阶段：实施 M3

用户确认后实施：
1. 复用现有领域模型和 FileSkillRegistry。
2. 建立 metadata-only Discovery。
3. 实现 Intent Matching、Candidate Ranking、Discovery Policy。
4. 支持 AUTO、RECOMMENDED、EXPLICIT、DISABLED。
5. 实现仅对最终选择项加载正文的 Lazy Loader。
6. 加载前二次校验 principal、owner、状态、版本、文件路径和摘要。
7. 保留旧四条提示并增加兼容测试。
8. 编写 M3 专项测试和文档。

完成 M3 后先运行 M3 测试并生成阶段报告。不得因为进入 M4 而跳过 M3 验收。

## E. 第三阶段：实施 M4

在 M3 验收通过后：
1. 复用已有任务激活状态模型；没有缺口不创建重复状态机。
2. 实现任务级 Skill Activation 和不可变版本快照。
3. 实现结构化 Context Builder。
4. 实现受限 Resource Resolver。
5. 实现 Runtime Lifecycle、Dispatcher、Result Handler 和错误恢复。
6. 通过统一应用层服务接入 Chat 与 Agent。
7. 保留原 Chat/Agent 默认行为和四条旧提示兼容。
8. 编写 M4 单元、集成、并发、异常和回归测试。

禁止重写 Agent Loop、Tool Executor、MCP、插件或 LLM Provider。

## F. 强制工程规则

- 先读后改；最小改动；不做无关重构。
- 不覆盖 `docs/phase3/`。
- 新文档放在 `docs/phase3-skill-system/` 下合适的新子目录，先检查是否已有同名文档。
- 不修改 MCP、插件、代码执行相关文件，除非审查证明必要且用户明确批准。
- 不擅自初始化 Git、提交、打标签或创建检查点。
- 不修改 `tests/database/test_metadata.py` 及 MCP 表来消除已知历史失败，除非用户另行授权。
- 不删除或迁移 `research`、`polish`、`minutes`、`weekly`。
- 不增加第三方脚本执行。
- 不把请求体中的用户 ID 当成权威身份。
- 不允许跨用户数据泄漏。
- 不把 Skill 指令当作系统指令。
- 不静默吞掉错误，不伪造测试结果。

## G. 测试规则

每批改动后运行最小相关测试；M3 完成跑 M3 测试；M4 完成跑 M4 和 Chat/Agent 集成测试；最后跑全量测试。

历史全量测试曾有两个失败，位于 `tests/database/test_metadata.py`，原因是旧表数量断言与 MCP 表不一致。必须重新运行确认；不得把历史结果冒充本次结果，也不得擅自修改该测试或 MCP 表。

所有测试结果记录命令、退出码、通过/失败/跳过数量和失败摘要。

## H. 交付物

1. M3_M4_IMPLEMENTATION_AUDIT.md
2. M3 设计/实现文档
3. M4 设计/实现文档
4. API/内部契约说明（若有）
5. 新增和修改的测试
6. M3 独立验收报告
7. M4 独立验收报告
8. Chat/Agent 集成报告
9. 全量测试报告
10. 变更文件清单与保护范围核对
11. 未解决问题、风险和后续 M5/M6 建议

## I. 验收与状态声明

分别给出：
- M3: PASS / FAIL / BLOCKED
- M4: PASS / FAIL / BLOCKED
- Chat integration: PASS / FAIL / BLOCKED
- Agent integration: PASS / FAIL / BLOCKED
- Full test suite: PASS / FAIL / BLOCKED

不得将 CODE PASS 等同于 PRODUCTION READY。M5 安全治理和真实部署验收尚未完成前，不得宣称整个 Skill System 生产就绪。

现在只执行 B 部分的只读审查。完成审查报告后暂停并等待用户确认。
