# 授权数据

新增三张表，用户列引用 `users.id`：

| 表 | 作用 | 唯一键 |
| --- | --- | --- |
| mcp_connector_grants | 用户是否还能使用这个连接器 | user_id + connector_id |
| mcp_tool_grants | 用户在该连接器上的单个工具 | user_id + connector_id + tool_name |
| mcp_authorization_audits | 授权变更动作 | 无业务唯一键，按用户和时间索引 |

表内使用时间戳，不用 JSONB。`expires_at` 和 `revoked_at` 为空表示这条记录仍然有效。审计表只记录用户、连接器、工具名和动作，不记录参数或令牌。

数据库启动检查关闭时，进程使用 `MemoryGrantStore`。检查打开并且数据库就绪时，进程使用 `SqlGrantStore`。两种实现的允许规则相同。
