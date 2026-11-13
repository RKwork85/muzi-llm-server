# MCP 验收报告

| 项 | 结果 |
|---|---|
| 验收结论 | FAIL |
| 总分 | 46 / 100 |
| 档位 | 不合格 |
| 一票否决 | 有 |
| 生产部署 | 不允许按当前实现上线 |
| 审计范围 | 工作区 Client。MCP Server 角色为 N/A |
| 代码变更 | 无生产代码变更。新增本目录报告和审计 Skill |

## 为什么不是 NOT IMPLEMENTED

存在可执行的客户端：stdio 和远程 HTTP 都能在假 Server 上完成握手和 `tools/call`。13 项现有测试通过。不能说项目没有 MCP。

## 为什么不是 CONDITIONAL PASS

条件通过需要没有一票否决，且分数至少进入有条件合格以上。当前 46 分，并且 MCP-001 与 MCP-003 已经满足规范中的否决描述：令牌可能被送到错误服务，HTTP 状态可能跨用户。

## 为什么不是 PASS

最新协议不兼容，legacy 版本协商不完整，Resources/Prompts 虽未虚假声明但 Tools 语义被削弱，AgentRuntime 未接入用户级远程工具，取消和观测都不足。

## 验收时核对过的证据

- `app/tools/mcp_client.py`
- `app/mcp/http_client.py`
- `app/mcp/service.py`
- `app/mcp/catalog.py`
- `app/mcp/crypto.py`
- `app/mcp/store.py`
- `app/api/v1/endpoints/mcp.py`
- `app/api/v1/endpoints/chat.py`
- `app/services/chat_service.py`
- `app/services/tool_service.py`
- `app/agents/toolsets.py`
- `app/main.py`
- `requirements.txt`
- `tests/agents/test_mcp.py`
- `tests/test_remote_mcp.py`
- 官方规格 `2026-07-28` 的 specification、versioning、authorization 页面

## 未验证因而没有给 PASS 的部分

- 真实 Notion MCP 与真实 OAuth。
- Alembic 迁移 `b7e1c0a94d11` 的实际数据库。
- 多副本和多进程下的会话一致性。
- 全量历史测试套件。

## 复验入口

P0 修复后至少要看到：

1. 恶意 `token_endpoint` 收不到授权码。
2. 用户 B 的请求不携带用户 A 的 Cookie。
3. 第二个用户不能使用未被自己授权的 stdio 工具，或部署被明确限制为单租户且默认关闭。
4. 原有 13 项 MCP 测试仍然通过。
5. 重新填写本目录评分表。在否决项关闭前，总分提高也不能改成生产合格。
