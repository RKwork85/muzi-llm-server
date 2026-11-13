# M1-VS01 人工验收

状态：READY_FOR_UAT。未经人工确认，不能标为 ACCEPTED。未提交、未推送。

## 1. 实际修改文件

后端 `muzi-llm-server`：

- `app/api/v1/endpoints/me.py`：`get_my_chat_run`
- `app/authorization/application/chat_run_detail.py`：`load_chat_run_detail`、`assemble_chat_run`
- `app/schemas/chat_run.py`
- `app/core/exceptions.py`：`ChatRunNotFoundError`
- `app/services/history_service.py`：历史快照保留 `chatRunId`
- `app/api/v1/endpoints/chat.py`：`_persisted_stream` 在把 `done` / `error` 发给客户端之前提交终态事件
- `tests/test_chat_run_detail.py`
- `tests/test_history_chat_run_id.py`
- `tests/api/test_chat_run_detail_api.py`

前端 `muzi-llm-frontend`：

- `src/modules/conversation/components/ChatSession.vue`
- `src/modules/conversation/components/RunDetailTrigger.vue`
- `src/modules/conversation/components/RunDetailPanel.vue`
- `src/modules/conversation/services/chatRunDetail.service.js`
- `src/api/rbac.js`：`fetchMyChatRun`

SEC-P0-01 / SEC-P0-02 的未提交改动仍在工作区，这次没有覆盖。`ExecutionGuard` 仍由 `ToolService` 和 `ToolExecutor` 调用。

## 2. API

新增只读接口：

```http
GET /api/v1/me/chat-runs/{run_id}
```

必须带 Bearer。查询当前用户的 `sessions.user_id`，再按 `plan_events.payload.run_id` 读取 `chat.run.started`、`chat.done`、`chat.error`、`chat.cancelled`。用量来自同一用户、同一会话、结算键 `{run_id}:` 前缀的 `llm_usage_records` 与 `usage_records`。不调用 `QuotaService.settle`。不存在或其他用户返回 404，未登录返回 401。

现有 `GET /api/v1/me/usage/analysis` 不能按单条 Run ID 查询，所以没有复用它作为消息详情。

## 3. 前端组件

```text
ChatSession.vue
  └── 现有助手消息
       └── RunDetailTrigger.vue
            └── RunDetailPanel.vue
                 └── chatRunDetail.service.js
                      └── rbac.js fetchMyChatRun
```

入口在助手消息底部，位于原有回答脚注之后。只有已登录且该条消息有 `chatRunId` 时显示。

## 4. 真实字段

- 运行标识：前端 `client_run_id`，写入 `plan_events.payload.run_id`
- 会话：`sessions.id`，事件外键 `plan_events.conversation_id`
- 开始/结束：`plan_events.occurred_at`
- 状态：终态事件类型。没有终态时为 `running`
- Token：`llm_usage_records.input_tokens`、`output_tokens`、`reasoning_tokens`、`total_tokens`
- 额度：`usage_records.amount`，只加 `result = settled` 的行
- 工具：`tool_usage_records`，只返回名称、状态、耗时和外部成本
- 消息上的 Run ID：本地 `chatMessages[].chatRunId`；云端历史放在 `chat_history_messages.planning.chatRunId`

没有新增 `runs` 表，也没有改结算键。

## 5. 启动命令

数据库容器已在运行：`127.0.0.1:5432`、`127.0.0.1:6379`。

```powershell
cd muzi-llm-server
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

```powershell
cd muzi-llm-frontend
npm run dev -- --host 127.0.0.1 --port 5500
```

这次验收时两个进程都已按上面的地址启动。Vite 把 `/api` 代理到 `8001`，见 `vite.config.js`。

## 6. 页面地址

http://127.0.0.1:5500/

对话页形如：

http://127.0.0.1:5500/chat/{chatId}

## 7. 点击路径

1. 使用已有登录状态打开首页。
2. 确认模式是 Ask。
3. 输入问题并发送。页面进入 `/chat/{chatId}`。
4. 等助手回答出现。
5. 点击该条回答下的「运行详情」。
6. 再点「收起运行详情」可关闭。

## 8. 两轮验收

在同一对话继续发送第二条。两条助手消息各自有「运行详情」。运行标识不同，会话标识相同，Token 分别对应该轮。

本次浏览器实测：

- 第一轮总 Token 3,170
- 第二轮总 Token 3,885
- 服务重启后的第三轮状态为「已完成」，总 Token 4,071

## 9. 刷新

刷新 `http://127.0.0.1:5500/chat/{chatId}`。两条已完成的助手消息仍各自带原来的 Run ID，详情仍向后端读取。本次刷新后两个 Run ID 都还在，两个按钮都在。

## 10. 只读 SQL

把 `:run_id` 换成消息详情里的运行标识。

```sql
SELECT event_type, occurred_at
FROM plan_events
WHERE payload->>'run_id' = :run_id
ORDER BY sequence;

SELECT settlement_key, amount, result
FROM usage_records
WHERE settlement_key LIKE :run_id || ':%';

SELECT settlement_key, model, input_tokens, output_tokens, reasoning_tokens, total_tokens
FROM llm_usage_records
WHERE settlement_key LIKE :run_id || ':%';
```

实测两轮 API 请求各有 1 条 `chat.run.started`、1 条 `chat.done`、1 条 `usage_records` 和 1 条 `llm_usage_records`。重复读取详情不会增加金额。

## 11. 测试结果

- 后端详情、历史和安全相关 pytest：29 passed
- 前端 `npm run build`：成功
- `git diff --check`：无空白错误
- API 联调：登录后两轮 DeepSeek Ask，详情 200，状态 `completed`，模型 `deepseek-flash`，Token 3567 与 3305；再次读取额度不变
- 另一注册用户读取第一轮：404，响应不含 usage
- 不带 Bearer：401
- 读到 `done` 后立即断开：详情状态 `completed`，Token 3157，只有一轮结算
- 浏览器：首页发送、第二轮、刷新、展开详情均走过。修复终态提交后，新的一轮详情显示「已完成」

没有用 Mock 代替上述联调。

## 12. 未解决

- 未登录 Ask、方舟流不会生成可查询的 Run ID，消息上不显示入口。
- 修复前已经结束、但库里没有 `chat.done` 的一轮，详情会保持「进行中」。本次浏览器里的第二轮就是这种情况；它的 Token 仍是真实记录。
- 没有在真实上游故障下做一次模型失败。失败状态由组装测试覆盖：`chat.error` 不会显示为已完成。
- 云端历史的字段保留有单元测试。这次没有用第二台浏览器验证跨设备同步。
- 思考 Token 只在 `llm_usage_records.reasoning_tokens` 有值时显示，否则是「未记录」。
- 一次 Ask 里的多次模型调用仍属于同一个 Run ID。本次实测每轮只有 1 次模型调用。

## 13. Git 工作区

两个仓库都在原分支，没有新的提交。

后端 HEAD：`4be86a97702cea679891394faf2a3daf1e9b6a27`

前端 HEAD：`a2812c760718e9fed5635db528aa5d6c0d02065e`

后端除本次文件外，仍有 SEC-P0 的已修改和未跟踪文件。不要把两组改动拆丢。
