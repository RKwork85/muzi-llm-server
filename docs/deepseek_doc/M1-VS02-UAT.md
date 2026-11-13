# M1-VS02 人工验收

状态：READY_FOR_UAT。未经人工确认，不能标为 ACCEPTED。未提交、未推送。

## 1. 修改文件

- `muzi-llm-server/app/chat/run_registry.py`：`ChatRunRegistry.is_active`
- `muzi-llm-server/app/authorization/application/chat_run_detail.py`：`resolve_chat_run_status`
- `muzi-llm-server/app/schemas/chat_run.py`：增加 `unknown`、`status_reason`、`status_source`
- `muzi-llm-server/tests/test_chat_run_detail.py`
- `muzi-llm-server/tests/test_chat_run_registry.py`
- `muzi-llm-frontend/src/modules/conversation/components/RunDetailPanel.vue`

没有修改 `QuotaService.settle`、消息正文、SSE 事件名或额度分析接口。

## 2. 状态判定

按最后一条终态事件决定，不改旧事件：

| 证据 | status | status_reason | status_source |
|---|---|---|---|
| 最后一条是 `chat.done` | completed | `terminal_event`，多条终态时为 `latest_terminal_event` | `plan_events` |
| 最后一条是 `chat.error` | failed | 同上 | `plan_events` |
| 最后一条是 `chat.cancelled` | cancelled | 同上 | `plan_events` |
| 没有终态，且本进程注册表里该用户的任务仍在执行 | running | `registry_active` | `run_registry` |
| 没有终态，注册表也没有活动任务 | unknown | `terminal_event_missing` | `historical_inference` |

有终态时，即使注册表仍标着活动，也以终态为准。服务重启后注册表清空，缺终态的历史记录变为 unknown，不会继续显示进行中，也不会因为已有 Token 被当成 completed 或 cancelled。

## 3. API 示例

`GET /api/v1/me/chat-runs/06c46f3d-e509-423b-9e50-6d5ab666749f`

```json
{
  "status": "unknown",
  "status_reason": "terminal_event_missing",
  "status_source": "historical_inference",
  "usage": {
    "total_tokens": 3885,
    "quota_consumed": 3885
  }
}
```

新完成的一轮返回 `status: completed`、`status_reason: terminal_event`、`status_source: plan_events`。其余原有字段不变。

## 4. 测试结果

- pytest：`tests/test_chat_run_detail.py`、`tests/test_chat_run_registry.py`、历史与详情 API、Agent 权限相关测试，27 passed。
- 真实历史轮 `06c46f3d-e509-423b-9e50-6d5ab666749f`：`unknown`，Token 3885。再读一次，用量不变。
- 匿名请求 401。另一注册用户 404，响应不含 usage。
- 新的一轮在流式过程中读到 `running`，结束后是 `completed`，Token 3452。再读一次额度不变。
- 同一会话第二轮也是 `completed`，Run ID 不同，会话 ID 相同。
- 浏览器打开 http://127.0.0.1:5500/chat/LpGKf7MJuTHiL3kh8aV6xwL4 ，缺终态的那一轮显示「运行结果未知」和说明，Token 仍是 3,885，没有显示失败或已完成。刷新后一致。
- http://127.0.0.1:5500/me/usage 仍能打开额度分析，没有请求失败。

## 5. 页面入口

助手消息底部的「运行详情」，组件是 `RunDetailPanel.vue`。

当前页面：http://127.0.0.1:5500/chat/LpGKf7MJuTHiL3kh8aV6xwL4

API：http://127.0.0.1:8001

## 6. 只读 SQL

```sql
SELECT event_type, sequence, occurred_at
FROM plan_events
WHERE payload->>'run_id' = '06c46f3d-e509-423b-9e50-6d5ab666749f'
ORDER BY sequence;

SELECT settlement_key, amount, result
FROM usage_records
WHERE settlement_key LIKE '06c46f3d-e509-423b-9e50-6d5ab666749f:%';
```

这条历史运行有 `chat.run.started` 和用量，没有 `chat.done`、`chat.error` 或 `chat.cancelled`。

## 7. 人工验收

1. 打开上面的对话页，展开「给我一个实际应用案例。」那条回答的运行详情。
2. 确认标题是「运行结果未知」，并有「无法确认最终执行结果」的说明。
3. 确认总 Token 仍是 3,885。
4. 刷新页面后再打开，结果相同。
5. 再发一条新的 Ask。新回答的详情应为「已完成」，上一条仍是未知。
6. 打开「额度消耗分析」，确认页面仍能加载。

## 8. 已知限制

- unknown 只改变读取结果，不补写终态事件。
- `running` 只代表当前这个 API 进程里的活动任务。进程退出后，尚未写下终态的轮次会变成 unknown。
- 同一轮若先后写入多种终态，以序号最晚的一条为准。
- 没有取消事件时，不会显示已取消。

## 9. Git 摘要

后端 HEAD 仍是 `4be86a9`。本次相对工作区新增或改动：

- `app/chat/run_registry.py`：+7
- `tests/test_chat_run_registry.py`：+22
- `app/authorization/application/chat_run_detail.py`、`app/schemas/chat_run.py`、`tests/test_chat_run_detail.py` 仍是未跟踪文件，里面包含状态判定。
- 前端 `RunDetailPanel.vue` 仍是未跟踪文件，增加了 unknown 文案。

SEC-P0 的其他未提交文件都还在。没有 commit，也没有 push。
