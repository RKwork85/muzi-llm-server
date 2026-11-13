# M2-VS01 Ask 运行记录持久化

状态：**READY_FOR_UAT**。没有标记 ACCEPTED。没有 commit，也没有 push。

本阶段只持久化 Ask。Plan 和 Agent 没有写入这张表，也没有做跨进程取消。

## 1. 修改文件

后端：

- `app/models/ask_run.py`：新模型
- `app/models/__init__.py`：注册模型
- `alembic/versions/c5d8e2a14b70_ask_run_metadata.py`
- `app/chat/ask_runs.py`：打开运行、终态更新
- `app/api/v1/endpoints/chat.py`：在现有流开始前写入运行，终态提交后再发送 `done` 或 `error`
- `app/authorization/application/chat_run_detail.py`：新运行按内部 ID 读取，旧运行继续走原事件
- `app/core/exceptions.py`：重复标识的 409
- `app/services/history_service.py`：历史快照保留 `serverRunId`
- `tests/database/test_metadata.py`：登记 `ask_runs`
- `tests/test_ask_run_status.py`
- `tests/test_history_chat_run_id.py`
- `tests/database/integration/test_ask_run_persistence.py`

前端：

- `src/modules/conversation/components/ChatSession.vue`

没有改 `ChatService.iter_sse`、`_iter_plain_sse`、`_iter_ask_sse`、`DeepSeekProvider`、`ToolService`、`QuotaService.settle`，也没有改 SEC-P0 的权限判断。

## 2. 数据库

迁移版本：`c5d8e2a14b70`，上一版本 `b3e7a1c94d20`。

表 `ask_runs`：

| 列 | 含义 |
|---|---|
| `id` | 服务端生成的 UUID，可信运行标识 |
| `owner_id` | 已认证用户，删除用户时 RESTRICT |
| `conversation_id` | 会话，删除会话时 CASCADE |
| `surface` | 固定 `ask` |
| `external_id` | 客户端 `client_run_id`，没有时用本次 `request_id` |
| `external_id_type` | `client_run_id` 或 `request_id` |
| `request_fingerprint` | 用户正文的 SHA-256，不保存原文 |
| `status` | `running`、`completed`、`failed`、`cancelled`、`unknown` |
| `status_reason` | 原因 |
| `created_at` / `started_at` / `finished_at` | 时间 |

唯一约束是 `(owner_id, conversation_id, external_id)`。不同会话可以有相同的客户端标识。表内不存 Token、额度、提示词或回答。

本地库已执行 `alembic upgrade head`。

## 3. 新旧标识

| 名称 | 谁生成 | 用途 |
|---|---|---|
| `client_run_id` | 浏览器，仍写入消息的 `chatRunId` | 只做客户端关联 |
| `ask_runs.id` | 服务端 | 事件 `payload.run_id`、结算键前缀、详情查询、消息 `serverRunId` |
| 旧 `chatRunId` | 当时的客户端 UUID | 没有 `ask_runs` 行时，详情仍按这个值查 `plan_events` |

响应头 `X-Run-Id` 在运行行和 `chat.run.started` 提交之后才返回。SSE 事件名和顺序没有改。

同一会话的真实例子：

- 客户端 `ef413cae-5fa4-45a4-b73f-5e7244494c74`，服务端 `bb1d5952-f109-46b3-ae46-a5dd592515b3`
- 客户端 `48a365bd-40a4-4f1f-9292-81505f248d2b`，服务端 `9e35278f-944a-4ea5-9647-a7ca29330a56`

结算键分别是这两个服务端 ID 加轮次，没有串到另一轮。

## 4. 事务顺序

1. 校验登录、会话和额度。此处只授权，不结算。
2. 插入 `ask_runs`，状态 `running`。
3. 写入用户消息和 `chat.run.started`。`run_id` 用服务端 ID。
4. 提交。失败则不会打开 SSE，也不会宣称完成。
5. 沿用原来的模型流。每轮 `usage` 仍用 `{服务端运行 ID}:{轮次}` 结算。
6. 收到模型的 `done` 或 `error` 后，在同一事务里把运行行从 `running` 更新为终态，并写入终态事件。
7. 提交成功后，才把原来的 `done` 或 `error` 帧发给前端。
8. 流结束时如果还没有终态，提交 `cancelled`。这一步不补发成功的 `done`。

终态更新带条件 `status = 'running'`。已经完成的行不会被失败覆盖，也不能再写成 `running`。数据库里的 `running` 在读取时不等于任务仍活跃：本进程注册表没有该任务时，详情返回 `unknown`。

## 5. 重复 ID

同一用户、同一会话、同一个 `client_run_id`：

- 正文摘要相同且已经终态：不调用模型，不新增运行，不新增结算。响应头仍是原来的服务端 ID，只回放一条已经存在的终态。
- 正文摘要相同且仍是 `running`：HTTP 409，`ASK_RUN_IN_PROGRESS`。
- 正文摘要不同：HTTP 409，`ASK_RUN_CONFLICT`。不合并，不把另一轮的 Token 算进来。

真实请求核对：重放 `bb1d5952-f109-46b3-ae46-a5dd592515b3` 后额度仍是 3009，调用次数仍是 1，响应里没有 `delta`。换一句正文得到 409。

## 6. 兼容

- 新消息优先用 `serverRunId` 打开详情，同时保留 `chatRunId`。
- 旧消息没有 `serverRunId` 时，继续用原来的 `chatRunId`。
- 旧运行如果同一个 ID 出现在多个会话，详情返回 404，不再挑选最晚的一条。唯一的旧运行仍按 M1 规则判断状态。
- 没有把旧的未知运行改写成成功。页面上的 `06c46f3d-e509-423b-9e50-6d5ab666749f` 刷新后仍是「运行结果未知」。

## 7. 测试

真实 PostgreSQL，`MUZI_DB_TESTS=1`：

```text
pytest tests/database/integration/test_ask_run_persistence.py -q
6 passed
```

覆盖两轮、不同会话、重复客户端 ID、同一请求重放、终态竞争、未提交终态回滚、Token 不串轮、重复读取不增加用量、跨用户查不到、旧记录可读、重复旧 ID 不挑选。

另外：

```text
pytest tests/test_ask_run_status.py tests/test_history_chat_run_id.py tests/test_chat_run_detail.py tests/test_chat_stream_persistence.py tests/api/test_chat_run_detail_api.py tests/api/test_agent_access.py tests/api/test_tool_execution_auth.py -q
```

其中 `tests/database/test_metadata.py` 的两例仍然失败。失败原因是期望表清单里本来就没有计量相关表，而模型里已经有这些表。这次把 `ask_runs` 加进了清单，但没有顺手改写整份旧清单。

## 8. 真实联调

API `127.0.0.1:8001`，页面 `127.0.0.1:5500`，模型为已配置的 DeepSeek。迁移之后重启了 API。

会话 `07039ea6-5eed-48c9-8956-f396aa9c6ed2`：

- 两轮普通 Ask，服务端 ID 不同，状态都是 `completed`，来源 `ask_runs`。
- 计算器 Ask `a74f902c-ddc3-43e8-81be-7b1e8bf26ed5` 有 `tool` 事件，详情里的工具是 `calculator`，两轮 Token 都挂在这个服务端 ID 上。
- 重复读取详情，额度不变。
- 未登录 401。另一个新注册用户读取 404。
- `GET /api/v1/me/usage/analysis` 返回 200。

页面：

- 在 Ask 输入并发送后，运行详情显示服务端 ID `f137fa4a-b51f-4c80-bb84-3570e3a4cb8a`，状态「已完成」，总 Token 6,556，与消息上的本轮用量一致。工具为 `get_current_time`。
- 刷新 `http://127.0.0.1:5500/chat/NzznBNhER4SH9nU0NXgTzvIS` 后，同一 ID 和「已完成」仍在。
- 额度页标题为「额度消耗分析」，没有「请求失败」。
- 旧对话页的运行详情按钮还在。

## 9. 未验证

- 没有在真实模型流上进行中途取消，也没有故意让 DeepSeek 返回失败。这两种终态的数据库竞争和回滚已在 PostgreSQL 测试里核对。
- 没有做第二个进程取消正在跑的 Ask。
- `tests/database/test_metadata.py` 仍因原有计量表清单不完整而失败。

## 10. 回滚

1. 停掉使用新代码的 API。
2. 在 `muzi-llm-server` 执行 `alembic downgrade b3e7a1c94d20`，只删除 `ask_runs`。
3. 恢复本阶段改过的后端和 `ChatSession.vue`。

已有的 `plan_events` 和用量行不要删。旧详情不依赖 `ask_runs`。
