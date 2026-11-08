# Skill System 基线

核对日期：2026-11-08。

## 路径

实施说明里的项目路径是 `C:\Users\rkwork\Desktop\muzihhh\work\muzi_project\muzi-llm-server`。当前工作区里的后端是：

`C:\Users\rkwork\Desktop\muzihhh\work\muzi_project\muzi-llm\muzi-llm-server`

后续文档和代码都指向这个目录。`docs/phase3/` 是更早的能力扩展记录，不是本次 Skill System，不覆盖那些文件。

## Git

在 `muzi-llm` 和 `muzi-llm-server` 执行 `git status`、`git rev-parse`、`git branch`、`git log`，结果都是 `not a git repository`。

因此本次不能建立提交检查点，也不能判断哪些未保存修改属于哪一次历史提交。按实施说明，归属不清的修改先暂停提交。现有源码保持不动。

## 已有“技能”不是 Skill System

`app/prompts/skills.py` 只有四条写死的提示词：`research`、`polish`、`minutes`、`weekly`。`skill_notes()` 把它们拼进对话系统提示。`ChatService._mode_prompt()` 在 Plan 模式把这些文字标成计划参考，在 Agent 模式标成可执行做法。

这不是注册表、发现、加载、权限或持久化。前端个性页里的技能卡片也只改浏览器本地勾选，不读取 `SKILL.md`。

## 测试

本次 M0 没有改运行代码，也没有重跑全量测试。沿用最近一次在本目录执行的相关结果只能说明当时的工具和 MCP 测试通过，不能当作 Skill System 的验收。M1 开始前要按仓库里的 `pytest` 命令跑当前全量测试，并记下跳过项。
