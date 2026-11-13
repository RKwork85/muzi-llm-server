# M2.1 用户验收

状态：**READY_FOR_UAT**。请人工验收后再决定是否进入 M2.2。本文不代表 ACCEPTED。

本阶段没有新页面、新接口、新表，也没有改变聊天、计划和 Agent 的执行结果。

## 验收范围

确认三件事：

1. 同一会话的两轮 Ask 会被看成两个运行号。
2. 重复的 Ask 运行号会被报告为歧义，而不会被自动选中一条。
3. Plan 的公开号、修订号、模型调用，以及 Agent 的任务号和 execution，没有被并成同一个 ID。

## 自动化

在 `muzi-llm-server` 执行：

```text
pytest tests/test_run_identity.py -q
```

期望：`17 passed`。

其中应能看到这些行为：

- 同一会话两个 Ask 运行号都是 `resolved`，结算键互不占用。
- 同一运行号出现两次开始事件时，结果是 `ambiguous`，`conversation_id` 为空。
- 其他用户的事件和用量不会出现在结果里。
- 一个 Plan 的修订号列表与单次结算键是不同对象。
- 用 execution 的 UUID 查询任务时，结果是 `unresolved`，原因 `execution_id_is_not_task_id`。
- 无会话 Agent 的 `durable` 为 false，且没有会话和 execution。
- `GET /api/v1/run-identity` 为 404。

## 只读数据库核对

不要为验收插入 Plan 或 Agent。使用当前库的只读查询即可。

两轮 Ask 应落在同一会话、两个运行号：

- 会话 `9031058b-b2c5-4280-a34c-b2cb9bc3f77e`
- 运行 `58040e9d-ee17-47f5-a4eb-17b8f451cb4b`
- 运行 `3cc84685-f90e-4f4e-8a9f-f9f8f260979c`

一个真实 Plan：

- 公开号 `plan_69c623cc65904e26bca52d70ba085845`
- 当前修订 3
- 修订历史 0、1、2、3
- 内部主键 `e3c9b593-ab39-48a0-91b5-e8dd3ae0cef4`
- 交互卡 2 张

有会话 Agent：当前 `agent_tasks` 和 `executions` 都是 0。这一项是 **NOT_VERIFIED**，不要补一条假任务来通过验收。

Plan 模型调用：当前没有 `source = planning` 的用量行。这一项也是 **NOT_VERIFIED**。

## 不应出现的变化

- 没有新的数据库表或 Alembic 版本。
- Ask、Plan、Agent 的 SSE 事件名和顺序不变。
- `QuotaService.settle` 和三类结算键字符串不变。
- 对话页不需要改交互。
- 重复的 `client_run_id` 仍然可以提交；本阶段只在对照结果里报告冲突。

## 已知差距

重复 Ask 运行号时，新对照返回歧义；现有运行详情接口仍选择最晚的一条。验收时不要要求这两个结果已经相同。
