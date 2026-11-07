# 数据库基础设施实施方案

执行日期：2026-11-07。根目录是 `muzi-llm-server`。

## 冲突和取舍

1. `docs/phase3/` 已属于 Agent Runtime，不能覆盖。数据库文档放在 `docs/database/`。
2. `app/data/` 是文件暂存，不是 SQL。连接层放在 `app/core/database/`，表放在 `app/models/`。
3. `TaskManager` 和 `EventPublisher` 继续负责进程内状态和事件分发。新表只保存记录，不在请求里推进状态，也不改 Runtime、Registry、Executor、Policy。
4. 现有测试默认不连接数据库。`DATABASE_STARTUP_REQUIRED` 默认 `false`。本机 `.env` 在密码生成后设为 `true`，启动时会检查 PostgreSQL 和 Redis，失败则拒绝启动。
5. 时间列使用 `timestamptz`，PostgreSQL 按 UTC 保存。数据库和容器时区是 `Asia/Shanghai`，查询和 DBeaver 默认显示上海时间。应用写入带 UTC 时区的时间。
6. 用户密码用 Argon2id。刷新凭证是高熵随机串，只存 SHA-256，不用 Argon2。
7. PostgreSQL 和 Redis 互不依赖，Compose 不虚构 `depends_on`。两者都有健康检查。FastAPI 自己重试，不把容器健康检查当作连接保证。
8. 没有把 FastAPI 放进 Compose。Windows 本机连接 `127.0.0.1`。以后进入 `muzi-backend` 网络时，只改 `POSTGRES_HOST=postgres` 和 `REDIS_HOST=redis`。
9. Redis 密码由入口脚本写入容器内临时配置，不出现在 `docker compose` 的命令参数里。
10. 应用启动不跑 Alembic。

## 阶段

| 阶段 | 内容 | 结果 |
|---|---|---|
| M0 | 审计现有应用、配置、Lifespan、Agent 和测试 | `docs/database/BASELINE.md` |
| M1 | PostgreSQL 18 与 Redis 7 | `deploy/docker/` |
| M2 | 异步引擎、Redis、Lifespan、配置 | `app/core/database/` |
| M3 | 九张表和 ER 图 | `app/models/` |
| M4 | Alembic 异步迁移 | 版本 `6de1140597d2` |
| M5 | Windows 备份、恢复、02:00 计划任务 | `deploy/docker/scripts/` |
| M6 | 安全配置和依赖 | Argon2id、参数化 SQL、日志脱敏 |
| M7 | 测试和文档 | 见 `TEST_REPORT.md` |

## 删除策略

用户删除被会话和任务挡住，避免一次删掉历史。凭证和刷新凭证随用户级联删除。消息随会话级联。执行、工具调用和事件随任务或执行记录级联，因为它们不能脱离所属记录存在。

`updated_at` 由数据库触发器 `muzi_set_updated_at` 维护。消息、事件和刷新凭证是追加记录，没有 `updated_at`。

## 不在本次范围内

- 用户注册和登录接口
- 把 Agent 每次运行自动写入数据库
- 修改现有 Chat Completions 和 SSE 协议
- 初始化管理员账号
