# 整改计划

本文件只排顺序和验收方法。第一轮审计没有改生产代码。实施前需要单独授权。

## P0 安全阻断

### R-001 绑定 OAuth 端点

- 关联：MCP-001、MCP-002。
- 根因：发现到的 URL 被直接当作可请求目标，授权响应没有 `iss` 校验。
- 方案：只接受与已校验 issuer 同源的 authorization、token、registration 端点；拒绝非 https、内网、回环和链路本地地址；把 issuer 与 state 一起保存；回调按 RFC 9207 比较 `iss` 后再换 token。
- 影响：`app/mcp/service.py`、`app/api/v1/endpoints/mcp.py`、`tests/test_remote_mcp.py`。
- 测试：元数据把 token 端点指到另一个主机或 `http://127.0.0.1` 时必须失败，且测试替身收不到 code。`iss` 不匹配时不得调用 token 端点。
- 状态：未开始。

### R-002 按用户隔离 HTTP 状态

- 关联：MCP-003。
- 根因：全局 `AsyncClient` 的 Cookie 罐跨用户复用。
- 方案：关闭 Cookie 持久化，或按用户使用独立客户端；确认 `Set-Cookie` 不会出现在其他用户的下一个请求里。
- 影响：`app/main.py`、`app/mcp/http_client.py`。
- 测试：用户 A 的响应写 Cookie 后，用户 B 的请求头不得包含该 Cookie。
- 状态：未开始。

### R-003 明确 stdio 的租户边界

- 关联：MCP-004。
- 根因：一个子进程和一份工具表被所有 agent 用户共享。
- 方案：要么文档和启动检查把 stdio MCP 限制为单租户，要么改成按用户启动和授权。在多用户模式未完成前，多用户部署应保持 `MCP_ENABLED=false`。
- 影响：`app/main.py`、`app/agents/toolsets.py`、部署说明。
- 测试：两个用户身份请求 agent 对话时，不能调用对方未获授权的本地工具。
- 状态：未开始。

## P1 协议与权限

### R-004 写明并实现一个协议代际

- 关联：MCP-006。
- 根因：代码固定旧握手，同时部分使用较新的 HTTP 头，两边都不完整。
- 方案：若继续支持 legacy，校验 `protocolVersion` 只接受已实现的版本，并在不支持时断开。若要支持 `2026-07-28`，按官方版本页做 `server/discover` 或直接发送带 `_meta` 的无状态请求，处理 `-32022`，去掉对 `Mcp-Session-Id` 的依赖，补上 `Mcp-Method` 和 `Mcp-Name`。
- 影响：`app/tools/mcp_client.py`、`app/mcp/http_client.py` 及对应测试。
- 测试：假 Server 返回未知版本时客户端失败。现代假 Server 不接受 initialize 时，客户端改发无状态 `tools/list`。
- 状态：未开始。

### R-005 用协议 annotation 区分副作用

- 关联：MCP-005。
- 根因：写入和幂等来自工具名正则。
- 方案：默认不把未知工具标成幂等只读。使用 `readOnlyHint`、`destructiveHint`；目录 `allow_write=false` 时拒绝没有只读标注的工具。
- 影响：`app/mcp/service.py`、`app/tools/mcp_client.py`、`ToolRegistry` 的注册约束。
- 测试：名为 `execute` 且无只读标注的工具在 `allow_write=false` 时不可选。
- 状态：未开始。

### R-006 让两条 Agent 路径使用同一 MCP 视图

- 关联：MCP-007。
- 根因：远程工具只在 Chat SSE 里叠加。
- 方案：用户级 `tool_specs` 进入实际执行工具的那一层，并继续经过现有 allowlist 和超时。不另建 Registry。
- 影响：Chat 与 `app/agents` 的工具装配。
- 测试：同一用户在对话和任务接口看到同一组已选远程工具；另一用户看不到。
- 状态：未开始。

## P2 可靠性

### R-007 取消、并发和错误码

- 关联：MCP-009、MCP-010。
- 方案：超时发送取消或中止 HTTP；SSE 会话串行化；保留 JSON-RPC code 到内部错误类型；schema 至少保留 type、enum、required 和 description。
- 测试：并行两个 SSE 工具调用不串响应；超时后假 Server 收到取消或连接中止。
- 状态：未开始。

### R-008 换成标准密封

- 关联：MCP-008。
- 方案：用平台提供的 AEAD 保存 token 和 client secret，密钥与 MAC 密钥分离。提供离线迁移，不在审计轮次里改数据库。
- 测试：旧密文可识别；错误密钥不能解密；日志和异常不含明文。
- 状态：未开始。

## P3 运行与完整度

### R-009 指标、健康和依赖锁定

- 关联：MCP-013、MCP-015。
- 方案：记录 server、transport、版本、延迟和错误类型，不记录令牌。MCP 失败与进程存活分开报告。决定是否引入官方 SDK 后锁定版本。
- 状态：未开始。

### R-010 Resources 与 Prompts

- 关联：MCP-016。
- 方案：维持关闭，直到有产品需求。不要在 capabilities 里提前声明。
- 状态：保持未实现即可。

## 建议实施顺序

1. R-001、R-002、R-003。
2. R-004、R-005。
3. R-006。
4. R-007、R-008。
5. R-009。R-010 视产品范围决定。

每项合并前重跑 `tests/agents/test_mcp.py` 和 `tests/test_remote_mcp.py`，并补上该问题自己的失败用例。不要为了让旧断言变绿而放宽假 Server。
