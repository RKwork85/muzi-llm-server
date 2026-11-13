# 测试报告

日期：2026-11-07。解释器是本机 Miniconda Python 3.13。数据库是 Docker Desktop 中的 PostgreSQL 18.6 和 Redis 7.4.12。

## 数据库单元测试

```text
python -m pytest -q --tb=line tests/database --ignore=tests/database/integration
24 passed in 1.90s
```

覆盖密码哈希、刷新凭证哈希、连接串编码、配置不泄漏、JSON 脱敏、九张表的元数据、Compose 端口和卷路径、30 天保留逻辑，以及未开启数据库时 Lifespan 不建连接。

## 数据库集成测试

```text
$env:MUZI_DB_TESTS = "1"
python -m pytest -q --tb=short tests/database/integration
12 passed in 26.82s
```

| 项目 | 结果 |
|---|---|
| 两个容器健康 | 通过 |
| 端口只绑定 `127.0.0.1` | 通过 |
| 命名卷 `muzi-postgres-data`、`muzi-redis-data` | 通过 |
| 网络 `muzi-backend` | 通过 |
| `SELECT 1`、插入、更新、删除 | 通过 |
| 事务回滚和依赖提交 | 通过 |
| 唯一约束、外键、Argon2 检查约束 | 通过 |
| 用户删除被会话限制 | 通过 |
| 工具参数和事件载荷脱敏 | 通过 |
| `updated_at` 触发器 | 通过 |
| 连接池配置和多连接借出 | 通过 |
| 时区 `Asia/Shanghai` | 通过 |
| 数据目录位于 `/var/lib/postgresql/` 下的版本目录 | 通过 |
| Redis 密码、`SET`/`GET`、TTL | 通过 |
| AOF 与 RDB 配置 | 通过 |
| Redis 重启后键仍在 | 通过 |
| PostgreSQL 重启后行仍在 | 通过 |
| `alembic upgrade head` 与 `alembic current` 为 `6de1140597d2 (head)` | 通过 |
| `alembic check` 无结构差异 | 通过 |
| 手动备份、备份可列出、恢复到临时库、记录一致 | 通过 |
| 临时库上的 downgrade 再 upgrade，主库不受影响 | 通过 |
| 30 天清理 | 通过 |
| 备份日志不含数据库密码 | 通过 |
| `DATABASE_STARTUP_REQUIRED=true` 时 Lifespan 报告就绪，关闭后释放连接池 | 通过 |

## 回归

```text
python -m pytest -q --tb=line --ignore=tests/database/integration
1 failed, 229 passed, 9 skipped in 314.94s
```

229 条通过里包含上面的 24 条数据库单元测试。9 条跳过是原有的 live 测试，本次没有打开外部模型开关。

失败的一条是 `tests/api/test_agent_api.py::test_deadline_cancel_and_disconnect`。断言时期望任务仍是 `created` 或 `running`，实际是 `failed`，错误码 `UPSTREAM_TIMEOUT`。把 `app/main.py` 临时换回 Git HEAD 后，同一脚本得到同样结果：事件流关闭耗时约 5 秒，状态同样是 `UPSTREAM_TIMEOUT`。因此这不是数据库 Lifespan 引入的。事件流关闭会等到模型超时产生终态；测试设置里的模型超时是 5 秒。失败后测试没有放开阻塞线程，进程退出时出现一次 300 秒的线程池等待，所以整次回归花了大约 5 分钟。

本次没有改 Agent Runtime、Tool Registry、Tool Executor 或 ExecutionPolicy。Chat Completions、SSE 和工具调用的其余测试通过。

## 未完成

- 没有用户注册接口，也没有初始化管理员。
- Agent 运行还不会自动写入 `agent_tasks`。表已经对齐现有状态枚举，后续可以单独接持久化。
- `test_deadline_cancel_and_disconnect` 在本次改动之前就已经失败，留在原测试里，没有为了变绿而改断言或改运行时。
