# 数据库基础设施基线

核对日期：2026-11-07。执行根目录是 `muzi-llm-server`。`qs_llm_dp` 已弃用，本次不在那个目录里改代码。

## 与已有文档的冲突

`docs/phase3/` 已经是 Agent Runtime 阶段的记录，其中包括 `BASELINE.md`、`ACCEPTANCE.md` 和 `IMPLEMENTATION_LOG.md`。那些文件描述的是工具、联网和运行时，不是数据库。

如果按任务原文覆盖 `docs/phase3/BASELINE.md`，会丢掉已经核对过的 Agent 基线。因此数据库文档放在 `docs/database/`。Agent 代码和原有 phase 3 文档都没有改写。

## 已存在的能力

| 能力 | 位置 | 与本次的关系 |
|---|---|---|
| FastAPI 入口和 Lifespan | `app/main.py` | 继续使用这一个 Lifespan，只增加数据库启动和关闭 |
| 配置 | `app/core/config.py` 的 `Settings` | 扩展字段，不另建配置系统 |
| 日志脱敏 | `app/core/logging.py` | 把数据库密码加入现有过滤器 |
| 健康检查 | `app/api/health.py` | 保持原语义：`/health` 看进程，`/ready` 看 API Key |
| Agent Runtime | `app/agents/runtime.py` | 不修改 |
| 内存任务表 | `app/agents/task_manager.py` | 进程内状态机。注释写明重启后不保留 |
| 事件队列 | `app/agents/events.py` | 进程内分发。不改成数据库发布 |
| 工具注册表 | `app/tools/registry.py` | 不修改 |
| 工具执行器 | `app/agents/executor.py` | 不修改 |
| 执行策略 | `app/agents/policy.py` | 不修改 |
| 对话和 SSE | `app/api/v1/endpoints/chat.py` | 不修改协议 |
| 文件暂存 | `app/data/store.py` | 本地文件，不是 SQL。不把它当成数据库模块 |

领域状态已经存在，持久化表直接对齐它们：

- `TaskStatus`：`created`、`running`、`completed`、`failed`、`cancelled`、`limit_reached`
- `ToolExecutionStatus`：`succeeded`、`failed`、`timed_out`、`rejected`
- `AgentEventType`：`agent.started` 到 `agent.limit_reached`
- `TaskRecord` 还有 `request_id`、`error_code`，任务表因此保留这两列

## 当时还没有的能力

- PostgreSQL、Redis、Docker Compose、Alembic
- SQLAlchemy 引擎、会话和 Redis 客户端
- 用户、凭证、会话、消息、任务、执行、工具调用、事件、刷新凭证九张表
- Argon2id 和刷新凭证哈希
- `pg_dump` / `pg_restore` 和 Windows 计划任务

依赖文件原来只有 FastAPI、Uvicorn、OpenAI SDK、Pydantic、httpx 和 pytest。测试由 `pytest.ini` 的 `asyncio_mode = auto` 驱动。

## 可以复用的代码

- `Settings`、`safe_validation_text`、`RedactingFilter`
- `create_app` 的单个 Lifespan 和请求 ID 中间件
- `TaskStatus`、`ToolExecutionStatus` 的取值
- 现有测试夹具 `make_settings` 和 `FakeProvider`

## 新增模块

- `app/core/database/`：URL、引擎、Redis、依赖、启动关闭、脱敏
- `app/core/security.py`：Argon2id 和刷新凭证哈希
- `app/models/`：九张表
- `alembic/`：异步迁移
- `deploy/docker/`：Compose、Redis 入口、备份脚本

## 可能的重复

- `app/data/` 是文件暂存，不承担关系数据。
- `TaskManager` 继续负责任务状态机。`agent_tasks` 只保存记录，不在请求路径里推进状态。
- `EventPublisher` 继续负责进程内推送。`events` 表只保存需要留下的事实。
- 不新增第二套 Tool Registry 或执行策略。

## 对现有架构的影响

Lifespan 在原有资源关闭之后释放数据库连接。默认 `DATABASE_STARTUP_REQUIRED=false`，现有测试和未配置数据库的进程不会连接 PostgreSQL 或 Redis。本机 `.env` 在生成密码后把该开关设为 `true`，正式启动时会检查连接并在失败时拒绝启动。

应用启动不执行 Alembic。迁移必须手动运行。
