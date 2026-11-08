# R1 变更

只列本阶段为三个安全问题改动的文件。工作区里还有整改前已经存在的其他修改，那些文件没有当作 R1 变更。

## 新增

| 文件 | 原因 |
|---|---|
| `app/mcp/oauth_guard.py` | Issuer 精确比较、端点形状和地址检查 |
| `app/mcp/pool.py` | 按用户和 Connector 管理客户端，并在连接时钉住已检查地址 |
| `app/tools/stdio_access.py` | stdio 目录和按用户授权 |
| `tests/test_mcp_r1_security.py` | 三个问题的攻击用例 |
| `docs/mcp-remediation/R1/` | 基线、方案、设计和验收 |

## 修改

| 文件 | 原因 |
|---|---|
| `app/mcp/catalog.py` | 目录可写可选 `issuer` |
| `app/mcp/service.py` | 元数据核对、绑定端点、拒绝带凭证的重定向、按用户取客户端 |
| `app/mcp/http_client.py` | RemoteHub 按用户和 Connector 加锁，并提供会话释放 |
| `app/main.py` | 用 `ClientPool` 替换全局 HTTP 客户端 |
| `app/api/v1/endpoints/mcp.py` | 从池取服务；回调读取可选 `iss` |
| `app/tools/mcp_client.py` | stdio handler 检查当前用户，注册时写入目录 |
| `app/agents/toolsets.py` | agent 工具名单按用户过滤 MCP |
| `app/services/chat_service.py` | agent 流带上当前 MCP 用户 |
| `app/api/v1/endpoints/chat.py` | 已登录 agent 对话建立该用户自己的 stdio 授权 |
| `app/agents/runtime.py` | 任务执行期间设置当前用户，并按用户过滤工具 |
| `app/api/v1/endpoints/agents.py` | 创建任务时传入已解析的用户 |
| `tests/agents/test_mcp.py` | 调用前授予测试用户，agent 流带上该用户的 Bearer |
| `tests/test_remote_mcp.py` | 假元数据补上 `issuer`，并可模拟坏端点和重定向 |

## 关键行为

- 未核对的端点不会收到授权码或 Refresh Token。
- 不同用户的 Cookie、HTTP 客户端和 Token 绑定分开。
- stdio 进程仍共享；工具能否执行看当前用户，不看上一个请求留下的全局身份。
- 未登录的 agent 对话不再列出 stdio 工具。
