# 问题清单

状态用审计规范枚举。本轮没有改代码，全部为开放问题。

| 问题 | 审计 ID | 等级 | 状态 | 位置 | 说明 |
|---|---|---|---|---|---|
| MCP-001 | SEC-01、SEC-22、A04-20 | CRITICAL | FAIL | `app/mcp/service.py` `_metadata`、`_token`、`_client_registration` | 元数据中的 issuer 和各端点未绑定，授权码、refresh token、client secret 可能被送到任意地址，并形成 SSRF |
| MCP-002 | SEC-06 | HIGH | FAIL | `app/api/v1/endpoints/mcp.py` `callback`；`McpService.finish` | 不记录、不比较 RFC 9207 `iss` |
| MCP-003 | SEC-17 | HIGH | FAIL | `app/main.py` `mcp_http`；`RemoteHub` | 全体用户共用一个会积累 Cookie 的 httpx 客户端 |
| MCP-004 | SEC-18、RT-05 | HIGH | FAIL | `app/main.py`、`app/agents/toolsets.py` | stdio MCP 是进程全局能力，agent 模式的每个用户都能调用 allowlist 中的工具 |
| MCP-005 | A05-11、SEC-21、REL-05 | HIGH | FAIL | `app/mcp/service.py` `_WRITE`；`register_mcp_tools` | 副作用只看工具名。未命中的工具按只读且幂等注册。不读 MCP annotations |
| MCP-006 | A01-08、A01-10、A01-11、A01-12 | HIGH | FAIL | 两个客户端的 initialize 常量 | 固定 `2024-11-05`，不校验返回版本，没有 2026-07-28 无状态请求 |
| MCP-007 | RT-13 | HIGH | FAIL | `app/agents/` 与 `chat.py` | 远程 MCP 只进流式 Chat，不进 AgentRuntime |
| MCP-008 | SEC-10 | MEDIUM | PARTIAL | `app/mcp/crypto.py` | 自研流密码和 HMAC 共用同一派生密钥，不符合常规密封实现 |
| MCP-009 | REL-03、REL-06、REL-09 | MEDIUM | FAIL | `mcp_client.py`、`http_client.py`、`tool_service.py` | 超时不发协议取消；SSE 会话不能安全并行 |
| MCP-010 | A05-04、A05-08 | MEDIUM | PARTIAL | `_schema`、结果解析 | schema 被削掉约束；非 text 结果被丢弃 |
| MCP-011 | SEC-07、SEC-08 | MEDIUM | NOT_IMPLEMENTED | `McpService.begin` | 无 scope，无 Client ID Metadata Documents。DCR 在 2026-07-28 已不是主路径 |
| MCP-012 | A02-08、A02-09 | MEDIUM | NOT_IMPLEMENTED | 两个会话实现 | 无重连、无池上限。死会话会被继续使用 |
| MCP-013 | OBS-03、OBS-04、DEP-03 | MEDIUM | NOT_IMPLEMENTED | 仓库依赖与日志 | 无 MCP 指标和链路，无官方 SDK 与锁文件 |
| MCP-014 | SEC-23 | LOW | PARTIAL | `list_connectors` | 未登录也可以看到连接器目录 |
| MCP-015 | DEP-02、DEP-06 | LOW | PARTIAL | `config.py`、`mcp.py` | 空 token key 不阻止进程启动；fallback HTTP 客户端可能不关闭 |
| MCP-016 | A06、A07 | INFO | NOT_IMPLEMENTED | 两个客户端的拒绝列表 | Resources 和 Prompts 未实现。与空 capabilities 一致，不是虚假声明 |

MCP-001、MCP-003 是一票否决的直接证据。MCP-004 在多用户部署中同样构成跨租户。单用户、且目录中的服务器完全可信时，风险会下降，但代码没有这种部署约束。
