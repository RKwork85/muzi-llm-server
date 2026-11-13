# R1 整改前基线

记录时间：2026-11-08。此文件写于代码修改之前，描述当时工作区，不引用旧项目路径。

## Git

| 项 | 值 |
|---|---|
| 分支 | `main` |
| HEAD | `8e123d100e5e89455782991fd41174ef5ac07735` |
| 工作区 | 不干净。存在用户未提交的修改和未跟踪文件 |

未跟踪的 MCP 实现包括 `app/mcp/`、`app/tools/mcp_client.py`、`app/api/v1/endpoints/mcp.py`、`tests/agents/test_mcp.py`、`tests/test_remote_mcp.py`。`docs/mcp-audit/` 也是未跟踪文件，报告存在，不是缺失。

本阶段不清理、不覆盖这些改动，也不提交。

## 审计报告

`docs/mcp-audit/` 中与本阶段相关的结论：

- `ISSUES.md`：MCP-001 端点未绑定，MCP-003 共用 Cookie 客户端，MCP-004 stdio 全局工具权限。
- `SECURITY_AUDIT.md`：这三项构成一票否决。
- `REMEDIATION_PLAN.md`：R-001、R-002、R-003。
- `FINAL_AUDIT_REPORT.md`：总分 46，结论 FAIL。

## 整改前结构

| 区域 | 位置 | 整改前行为 |
|---|---|---|
| OAuth | `app/mcp/service.py` `_metadata`、`_token`、`finish` | 从受保护资源元数据取 `authorization_servers[0]`，再请求该地址的授权服务器元数据。不核对文档里的 `issuer`。授权、令牌、注册端点未做独立安全校验。回调不读取 RFC 9207 `iss` |
| HTTP 客户端 | `app/main.py` `mcp_http`；`RemoteHub` | 一个 `httpx.AsyncClient` 给所有用户。Cookie 罐在这个客户端上 |
| stdio | `app/tools/mcp_client.py`；`app/agents/toolsets.py` | 一个全局子进程。`agent_chat_tool_names` 把已注册 MCP 工具加给所有 agent 对话，不看用户 |
| 工具执行 | `ToolService` / `ToolExecutor` | 按工具名和 allowlist 执行。stdio handler 不检查当前用户 |
| 存储 | `mcp_connections` 等 | 令牌密文按用户保存。本阶段不改表结构 |

## 当时测试

`tests/agents/test_mcp.py` 与 `tests/test_remote_mcp.py`。上一轮审计命令结果是 13 passed。测试使用假进程和假 HTTP，不访问真实 OAuth。

## 受影响的接口

对外 JSON 形状保持不变：`/api/v1/mcp/*`、`/api/v1/chat/completions/stream`、`/api/v1/agents/runs`。OAuth 回调会多读一个可选查询参数 `iss`，这是授权服务器按 RFC 9207 带回的参数，响应仍是原来的 302。
