# PostgreSQL 迁移检查

2026-11-08 对本机配置所指向的数据库做了只读检查：

- 端口可以连接。
- `alembic_version` 是 `b7e1c0a94d11`。
- `mcp_connector_grants`、`mcp_tool_grants`、`mcp_authorization_audits` 都不存在。

迁移文件 `c4a8e2b71f03` 的上一修订就是 `b7e1c0a94d11`，只创建这三张空表，并带有降级函数。

没有执行 `alembic upgrade`。当前环境找不到 `pg_dump`，任务要求在升级前备份。未备份的库不能当作已经验收。

因此真实迁移结果是 BLOCKED，不是 PASS。

升级前需要：

1. 用 `pg_dump` 或同等工具备份目标库。
2. 确认目标就是这份 `b7e1c0a94d11` 数据库。
3. 在该环境执行 `alembic upgrade c4a8e2b71f03`。
4. 复核三张表的字段、唯一约束、索引和外键。
5. 写入一条授权，重新连接后仍能读到，再删除这条测试数据。

生产进程在数据库不可用时不会改用内存授权。`runtime_env=production` 且没有打开数据库启动检查时，进程直接拒绝启动。授权存储抛错时，工具调用按拒绝处理。
