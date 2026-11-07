# M3 开始前基线

记录时间：2026-11-06。本文件只记录版本保护结果。写入时尚未修改 M3 生产代码。

## 项目根目录

`C:\Users\rkwork\Desktop\muzihhh\work\muzi_project\qs_llm_dp`

## 当前分支

`main`

## 版本结论

M1、M2 的生产代码和测试已经在 Git 中，没有遗漏的源文件。工作区里未跟踪的是 M1/M2 审查报告和 Skill 3 文档，已在本基线前单独提交。`.env`、虚拟环境和缓存没有进入提交。

## 历史提交

| 标记 | SHA | 说明 |
|---|---|---|
| Phase 1 | `7063d99a5ff8ae71643d792d4543c872b5848ff7` | DeepSeek FastAPI Phase 1 v0.1.0 |
| Skill 路径 | `9b609ba` | Phase 2 skill 路径对齐。完整 SHA 以 `git rev-parse 9b609ba` 为准 |
| M1 | `a80a781` | Agent domain contracts、ExecutionPolicy、RunClock |
| M2 | `1f1751b7b056b47fb96700be174db65a9c637984` | ToolExecutor |
| 检查点 | `995a2078018797145fdc0dba36ff147a90c20c4d` | 审查报告和 Skill 3 |

`git show --stat` 确认：

- `7063d99` 包含第一阶段应用、测试和 `docs/phase2/BASELINE.md`。
- `a80a781` 只新增 `app/agents/__init__.py`、`models.py`、`policy.py` 和 `tests/agents/test_policy.py`。
- `1f1751b` 只新增 `app/agents/executor.py` 和 `tests/agents/test_executor.py`。
- 已跟踪的 Agent 文件只有上述六个。

标签 `v0.1.0-phase1-baseline` 是附注标签，指向的提交仍是 `7063d99a5ff8ae71643d792d4543c872b5848ff7`。本次没有移动标签。

## 补充提交

产生了补充提交。

提交 `995a2078018797145fdc0dba36ff147a90c20c4d`，说明为 `chore: checkpoint before Phase 2 M3 Agent Runtime`。

纳入的文件：

- `docs/phase2/REVIEW_REPORT.md`
- `phase-skills/deepseek-fastapi-phase3-skill/` 下的 SKILL、README、references、checklists、templates

这些文件不含密钥。没有使用 `git add .` 或 `git add -A`。

## M3 开始前恢复点

权威恢复点：

```text
995a2078018797145fdc0dba36ff147a90c20c4d
```

该提交包含 Phase 1、M1、M2、审查报告和 Skill 3，不包含 M3 实现。恢复命令：

```text
git checkout 995a2078018797145fdc0dba36ff147a90c20c4d
```

本记录文件在该提交之后入库。本文件所在提交的父提交必须是上面的 SHA，生产代码与该 SHA 相同。恢复整个仓库记录时，检出本文件所在提交即可。

M3 开发期间的代码修改留在工作区，不自动提交。丢弃尚未提交的 M3 修改并回到基线时，使用上面的恢复点。
