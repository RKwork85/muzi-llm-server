权限缺口是真实的。会话身份可以不迁数据就对齐。Run Core 只做关联，不合并三条运行时。

1. 权限问题是否真实存在
存在。 ExecutionGuard 没有调用点。agent.execute 不是 Agent 创建的必经检查。工具执行不校验用户权限码。

app/authorization/application/execution_guard.py 定义了 ExecutionGuard。全仓库没有 ExecutionGuard(。管理接口用的是 app/authorization/dependencies.py 的 require_permission，和这个类无关。

Agent 创建有两条路径，都在 app/api/v1/endpoints/agents.py 的 create_run：

请求带 conversation_id：登录，再 require_permission(..., "agent.execute")，再核对 sessions.user_id。
请求不带 conversation_id：AgentRequest.conversation_id 默认为空。数据库就绪时只做登录和 QuotaService.authorize。不检查 agent.execute。数据库未就绪时，这段登录代码不进入，运行直接交给 AgentRuntime.start。
同一缺口出现在聊天流。app/api/v1/endpoints/chat.py 的 stream_completion 只有 body.conversation_id 存在时才按模式检查 agent.execute 或 chat.access。mode == "agent" 且没有会话标识时，数据库就绪只检查登录和额度。ChatService 仍会按 chat_mode_tool_names(..., "agent") 放出 Agent 工具。

内存任务也没有归属校验。app/agents/task_manager.py 的 task_id 默认是 secrets.token_urlsafe(16)，不是 UUID。agents.py 的 _verify_persisted_owner 在 uuid.UUID(task_id) 失败时直接返回。因此未落库的运行，只要知道 task_id，查询、取消和事件订阅都不核对用户。已落库且 task_id 为 UUID 的任务会核对 agent_tasks.user_id。

工具执行链不看 RBAC：

app/agents/executor.py 的 ToolExecutor.execute 只拒绝 risk_level != "low"、不在服务端名单、不在本次名单的工具。函数没有 user_id。
app/services/tool_service.py 的 _allowed 同样只拒绝非 low。
app/tools/registry.py 的 register 在进程启动时拒绝非 low 和部分访问级别。这是注册期约束，不是每次调用的用户授权。
写文件、删除和命令由请求体 AgentRequest.grants 打开。app/agents/toolsets.py 的 agent_tool_names 看到 write_file、delete_file、run_command 就加入名单。这个名单来自客户端，不查权限表。
MCP 是例外。app/tools/stdio_access.py 的 allows 在没有 user_id 时返回假。app/tools/mcp_client.py 在调用前执行 stdio_permitted。用户身份来自服务端 ContextVar，不信任模型参数。
工作区目录来自请求头密钥的哈希，见 app/tools/workspace_scope.py。它隔离目录，不证明该用户拥有 agent.execute。
最小修复，仍不改三条运行循环：

create_run 在数据库就绪时，无论有没有 conversation_id，都先登录并要求 agent.execute。有会话时再校验该会话属于这个用户。
stream_completion 在 mode == "agent" 时同样先要求 agent.execute，再决定是否绑定会话。
TaskRecord 记下 owner_id。查询、取消、订阅在内存命中时比对所有者，不能因为 task_id 不是 UUID 就跳过。
grants 不再由请求体直接生效。服务端按权限决定能否加入写、删除和命令。ExecutionGuard 可以此时接到 ToolExecutor.execute 之前，但第 4 步应单独做，避免和前三步混在一次改动里。
2. 会话身份统一的可行方案
可以不迁移旧数据。统一标识用已经存在的 sessions.id。不新增映射表。

表	主键	和会话的关系	用户归属
sessions
UUID
规划侧会话本身
user_id 外键到 users.id，删除限制为 RESTRICT
messages
UUID
session_id 外键到 sessions.id
没有自己的 user_id，经会话归属
chat_history_sessions
另一套 UUID
业务键是 (user_id, chat_id)；server_conversation_id 是可空字符串，没有外键
user_id 外键，级联删除
agent_tasks
UUID
可空 session_id 外键到 sessions.id
user_id 外键，RESTRICT
executions
UUID
只外键到 agent_tasks.id
经任务归属，没有会话列
llm_usage_records
UUID
conversation_id 是可空 String(128)，没有外键
user_id 外键，级联删除
plans.conversation_id 和 plan_events.conversation_id 已经外键到 sessions.id。前端在 ChatSession.vue 里把 createPersistentConversation 返回的 conversation_id 存为 serverConversationId，历史保存时写入 server_conversation_id（app/services/history_service.py）。新的 Ask、Plan 在已登录时已经指向 sessions.id。

不需要映射表。(user_id, chat_id) -> sessions.id 已经放在 chat_history_sessions.server_conversation_id。再造一张表会和这列重复。

历史数据有三类歧义，所以不能把这列改成强制外键，也不能回写用量行：

server_conversation_id 为空的历史只有浏览器 chat_id。用量里对应 chat:{history_chat_id}，见 app/api/v1/endpoints/ark.py 和 chat.py 的 _usage_conversation_id。chat_id 只在单个用户内唯一。
llm_usage_records.conversation_id 同时存 UUID 和 chat: 前缀。usage_turns.py 能解析 UUID 才去连 sessions，其余靠 chat: 别名配对。孤立图片还会按时间猜测关联。
没有 conversation_id 的 agent_tasks.session_id 为空。这些行不能事后挂到某个会话上，除非另有可靠证据。
用户归属保持现有外键和查询条件：规划读取同时要求 sessions.user_id 与 plans.owner_id；历史查询带 user_id；用量查询带 user_id。统一读路径只接受两种键，并且都必须先按 user_id 过滤：sessions.id，或该用户名下的 chat:{chat_id}。不把 chat_history_sessions.id 当作会话标识。messages.session_id 与 chat_history_messages.session_id 指向不同父表，代码里不能混用。

3. 最小 Run Core 接口
Run Core 只记录「这次执行是谁的、属于哪个已有标识」。它不驱动 Ask、Plan、Agent，不代替 Plan 事件表，不把落库当成重启后续跑。


RunRef
  run_id            沿用 client_run_id、request_id 或 task_id
  surface           ask | plan | agent
  owner_id
  conversation_id   仅 sessions.id；没有则为空
  settlement_key    调用方已经生成的键，原样保存
RunCore
  open(ref)         记下开始
  close(ref, status)
  correlate(ref)    把 ref 指到已有 plan_events 或 agent_tasks
不新增第二套事件表，不新增第二套任务表。Ask 继续用现有 plan_events 里的 CHAT_RUN_STARTED。Agent 继续用现有 agent_tasks、executions、events。Plan 继续用 plan_events 的序号。ChatRunRegistry 和 TaskManager 仍只负责本进程取消和存活，不写入 Run Core 的恢复语义。

新增一个模块：app/runs/protocol.py，只放 RunRef 和上述三个操作。

修改三个入口，各加一次 open/close，不进入循环内部：

app/api/v1/endpoints/chat.py
app/api/v1/endpoints/planning.py 的消息入口
app/agents/persistence.py，在现有观察者上补关联
不改 ChatService、PlanningDecisionEngine、AgentRuntime、TaskManager、core/sse.py、agents/events.py、QuotaService.settle。

4. 必须保留的旧接口与兼容要求
POST /api/v1/chat/completions/stream 的 SSE 事件名和顺序不变。client_run_id 仍是取消标识。POST /api/v1/chat/runs/{run_id}/cancel 仍只取消当前用户的内存运行。
Plan 的提交、幂等键、expected_revision、GET /api/v1/plans/{plan_id}/events?after= 序号恢复不变。
POST /api/v1/agents/runs、查询、取消、/events 的响应字段不变。内存任务重启后仍然找不到，不能借 Run Core 假装能恢复。
QuotaService.settle 的键仍是 user_id + source + settlement_key。Agent 步结算仍是 {task_key}:{step}。Run Core 不得另写一笔 consume。
前端 serverConversationId 继续等于 sessions.id。历史里的 chat:{chat_id} 继续能在用量里对上，直到该行有了 server_conversation_id。
5. 第一项应该实施的开发任务
先补执行入口的身份和 agent.execute 校验。不要先做 Run Core，也不要先改会话表。

范围只包括：

app/api/v1/endpoints/agents.py
app/api/v1/endpoints/chat.py 中 mode == "agent" 的分支
app/agents/task_manager.py 增加 owner_id
不接 ExecutionGuard，不改工具名单规则，不改结算键，不改 SSE。

6. 该任务的真实验收方法
数据库就绪时，用两个已登录用户做下面四步。用户 A 有 agent.execute，用户 B 没有。

A、B 都不带 conversation_id 调用 POST /api/v1/agents/runs。A 得到任务。B 被拒绝，且 AgentRuntime 中没有新任务。
A 创建任务后，用 B 的令牌携带该 task_id 调用查询、取消和 /events。三处都拒绝。A 自己可以查询和取消。
B 带自己的 sessions.id，以 mode=agent 调用 POST /api/v1/chat/completions/stream。请求在流出 SSE 之前失败。A 走现有 Ask 流，事件顺序与改动前一致。
用同一 settlement_key 重复触发一次已有聊天结算。usage_records 仍只有一行，used_value 不增加。
未登录调用 POST /api/v1/agents/runs 在数据库就绪时被拒绝。生产配置不允许数据库未就绪启动；开发环境若数据库未就绪，该接口也应拒绝创建，而不是匿名执行。