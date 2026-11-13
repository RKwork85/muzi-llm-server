# R2.7 验收

总判定：BLOCKED。不是 PASS。不能进入生产就绪。

| 项目 | 判定 | 证据 |
| --- | --- | --- |
| 生产密钥规则 | 本地 PASS | 缺密钥、短密钥、占位密钥、开发编号都会拒绝；日志不含密钥 |
| 真实密钥注入 | BLOCKED | 没有生产进程 |
| 迁移文件 | 本地 PASS | `c4a8e2b71f03` 接在 `b7e1c0a94d11` 之后，带降级 |
| 真实数据库升级 | BLOCKED | 库在 `b7e1c0a94d11`，三张表不存在；没有 `pg_dump`，未升级 |
| 生产拒绝内存授权 | 本地 PASS | 生产配置且未要求数据库时，进程启动失败 |
| 健康检查 | 本地 PASS | 开发环境 `/ready` 仍为 ready；生产缺库时原因是 `database_unavailable` |
| 真实多实例 | BLOCKED | 未在两台进程上验证 |
| 压力三档 | BLOCKED | 探针明确标 `not_run` |
| 写入未知不重试 | 本地 PASS | 超时和取消后的写入审计为 `unknown` |
| 真实数据库或远程故障 | BLOCKED | 没有停止 PostgreSQL、Redis 或远程 MCP |

本地相关测试：`tests/test_mcp_r27_acceptance.py` 与截止时间、订阅断开测试一起通过。随后的 MCP、Chat 和 Agent 回归 53 项通过。
