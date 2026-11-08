# M4 设计

Chat 和 Agent 都调用 `SkillPromptAdapter`，里面只有一个 `SkillOrchestrator`。

运行阶段在 `app/skills/runtime_state.py`，和安装状态机分开：

```text
created → resolving → loading → active → completed
```

失败和取消是终态，不能再打开。

`ActivationStore` 把正文副本写在注册表旁的 `runtime/*.json`。键是用户、任务和技能。同一次任务重复激活返回同一条记录。版本或摘要不同则返回 `SKILL_ACTIVATION_CONFLICT`，旧副本保持不变。

上下文由 `ContextBuilder` 分成系统、对话、任务、技能、引用和约束。技能段落带固定边界，标明不能覆盖系统策略。超出字符预算返回 `SKILL_CONTEXT_BUDGET_EXCEEDED`，不截断后继续。

资源读取在 `app/skills/resources.py`。只接受包内的 `references`、`assets`、`templates`，拒绝脚本、`..`、绝对路径、盘符和 UNC。解析后的路径必须仍在包根目录内。

没有数据库迁移。激活记录是文件，不是新表。
