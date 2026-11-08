# R1 实施方案

三个一票否决项分开修，不重写 MCP Client、AgentRuntime、ToolExecutor 或 ToolRegistry，不改数据库，不新增依赖，不升级协议版本。

## 顺序

1. R1.1：先把 OAuth 的可信 Issuer 和端点策略定下来。授权码和 Refresh Token 都走同一套检查。
2. R1.2：再把远程 HTTP 客户端按用户和 Connector 拆开。Cookie 跟 Token 分开存放。
3. R1.3：stdio 子进程继续共享，用户授权改成独立表。执行时核对当前用户。

## R1.1

可信 Issuer 只来自目录：`connectors.toml` 的 `issuer`，没有则用资源 URL 的 origin。受保护资源元数据里的 `authorization_servers` 必须包含这个精确值。授权服务器文档里的 `issuer` 也必须与它精确相等。

Token、授权和注册端点再单独做 URL 与地址检查。端点和 Issuer 可以不是同一台主机。带授权码或 Refresh Token 的请求不跟随重定向。

绑定写进现有密文字段：state 里保存 verifier、issuer、token endpoint 和是否要求 `iss`；access token 密文里保存 token、issuer 和 token endpoint。刷新只使用这份已绑定的地址，不采用后来发现的新端点。

## R1.2

`ClientPool` 的键是 `(user_id, connector)`。每个键有自己的 `httpx.AsyncClient` 和 Cookie 罐。同一用户的同一 Connector 用自己的锁串行会话建立和 RPC。不同用户不共用这把锁。

断开连接或关闭服务时丢弃对应客户端。OAuth Token 仍在连接记录的密文里，不放进 Cookie。

## R1.3

stdio 子进程仍由应用启动，环境里没有用户凭证，所以进程可以共享。`StdioAccess` 保存目录和每个用户的授权。handler 读取 `CURRENT_MCP_USER`。对话和 AgentRuntime 在列出工具时按这个用户过滤。

未登录的 agent 对话不再看到 stdio 工具。已登录且还没有授权记录的用户，得到目录的一份副本。显式授权或撤销不会被这次复制盖掉。进程重新注册目录时清空授权，避免把上一个进程里的授权带过来。

## 不在本阶段

协议升到 2026-07-28、Resources/Prompts、自定义加密替换、指标、把远程 MCP 工具接进 AgentRuntime 任务循环、把用户授权写入数据库。
