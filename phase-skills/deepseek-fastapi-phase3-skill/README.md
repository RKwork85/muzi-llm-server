# Skill 3 — DeepSeek FastAPI Agent Runtime

这是用于 `qs_llm_dp` 项目的 M3 实施 Skill 压缩包。

## 使用方法

1. 在 Cursor 打开唯一项目目录：
   `C:\Users\rkwork\Desktop\muzihhh\work\muzi_project\qs_llm_dp`
2. 将本目录放入项目的 `phase-skills/deepseek-fastapi-phase3-skill/`（或 Cursor 当前配置的 skills 目录）。
3. 新建 Cursor Agent 对话。
4. 先让 Cursor 读取 `SKILL.md` 全文，再执行：
   `templates/CURSOR_START_PROMPT.md`
5. Cursor 必须先检查工作区和现有实现，按 M3.0 → M3.6 顺序实施，不得跳阶段。
6. 不允许将“设计完成”当作“编码完成”；本 Skill 是实施任务。

## 包含内容

- `SKILL.md`：完整实施规则、官方协议约束、阶段任务、测试门禁和验收标准。
- `references/DEEPSEEK_API_CONTRACT.md`：官方 DeepSeek API 关键契约及来源。
- `checklists/PHASE_ACCEPTANCE.md`：每阶段验收清单。
- `templates/CURSOR_START_PROMPT.md`：Cursor 启动指令。
- `templates/CURSOR_CONTINUE_PROMPT.md`：中断后续做指令。

## 重要说明

本 Skill 不是 DeepSeek 官方产品，也不保证在未知代码状态下零修改风险。Cursor 必须以项目真实代码、当前 SDK 版本和官方文档为准。不得虚构测试结果或 API 能力。
