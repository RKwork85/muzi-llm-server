# MCP 最终审计报告

审计日期：2026-11-08。规范：`phase-skills/qs-llm-dp-mcp-compliance-audit-skill/SKILL.md`。官方规格以当天读取的 `2026-07-28` 为准。代码证据来自工作区，不是 HEAD 里已经不存在的推测。

结论：**FAIL**。总分 **46/100**。存在一票否决项。未达到生产标准。

## 18 个必答问题

1. **是否真正实现了 MCP？** 实现了 legacy MCP Client 的 tools 子集。有 JSON-RPC、`initialize`、`notifications/initialized`、`tools/list`、`tools/call`。不是只给模型一个私有 HTTP API。
2. **Client 还是 Server？** 只有 Client。没有产品级 MCP Server。`/api/v1/mcp` 是本应用的连接管理 API。
3. **使用哪个协议版本？** 客户端写死请求 `2024-11-05`。HTTP 层会把服务端返回的版本原样放进 `MCP-Protocol-Version`。依赖里没有 SDK 版本。
4. **是否符合目标协议版本？** 不符合官方当前版本 `2026-07-28`。对 `2024-11-05` 也只是部分符合，因为没有版本校验。
5. **支持哪些 Transport？** stdio（换行 JSON 和 Content-Length）、Streamable HTTP POST、Legacy SSE。后两者由目录里的 `transport` 选择。
6. **是否支持 Tools？** 支持发现和调用，schema 与结果类型是裁剪过的。
7. **是否支持 Resources？** 不支持，并且会拒绝相关方法。
8. **是否支持 Prompts？** 不支持，并且会拒绝相关方法。
9. **身份认证是否合格？** 不合格。PKCE、state、资源指示和 Bearer 有实现和测试；issuer 绑定和 RFC 9207 没有。
10. **权限控制是否合格？** 部分合格。远程工具按用户勾选，ask/plan 不执行 MCP。stdio 工具是全局的，写入判断依赖工具名。
11. **是否存在安全风险？** 存在。见下方否决项。
12. **是否正确集成 Agent Runtime？** 部分正确。流式 Chat 使用现有 Registry 和 `ToolService`。`app/agents` 的任务运行时没有用户级远程 MCP。
13. **是否具备可靠的错误处理？** 不充分。超时存在，协议取消、错误码和 SSE 并发都不充分。
14. **是否有足够的测试？** 定向测试够用，威胁模型测试不够。13 项通过，全部是假 Server。
15. **是否达到生产部署标准？** 没有。
16. **当前总分？** 46。
17. **是否存在一票否决？** 存在。
18. **最需要优先整改的是什么？** 先阻止 OAuth 元数据把令牌送到任意端点，再隔离用户间的 HTTP Cookie 和 stdio 进程。

## 真实架构

```text
管理员配置
  ├─ MCP_COMMAND 绝对路径 -> 一个全局 stdio 子进程 -> agent registry
  └─ connectors.toml 的 https URL -> 每用户 OAuth -> RemoteHub

对话
  agent 模式 -> 全局 stdio 工具 + 当前用户已选远程工具 -> ToolService
  ask / plan -> 不含这些 MCP 工具

AgentRuntime 任务
  只有启动时注册的 stdio 工具，没有远程每用户工具
```

默认 `MCP_ENABLED=false`。远程目录当前只有 Notion，且允许写入。本轮没有连接 Notion。

## 协议

| 视角 | 判断 |
|---|---|
| 2024-11-05 tools 客户端 | 部分符合。握手和调用属实，版本协商缺失 |
| 2025 一代 Streamable HTTP | 部分符合。有 POST、Accept、Session Id，没有完整服务端流 |
| Legacy SSE | 有实现和本机假服务测试，属于兼容层 |
| 2026-07-28 | 不符合。无 `_meta` 协议字段，无 `server/discover`，仍依赖 initialize 和 `Mcp-Session-Id` |

## 安全否决

1. `McpService._metadata` 跟随授权服务器元数据，不校验端点主机。授权码和 refresh token 会被 POST 出去。这同时是跨服务转发和 SSRF。
2. 所有用户共用 `app.state.mcp_http`。httpx 会保存 Cookie。
3. stdio 会话和工具表属于进程，不属于用户。

令牌在数据库中不是明文。用户也不能在请求里提交 MCP 命令或服务器地址。这些控制成立，但不能抵消上面三项。

## 测试限制

`13 passed in 1.28s`。未访问外网 MCP，未跑数据库迁移，未跑全量回归。因此任何“与 Notion 生产服务器兼容”或“SQL 存储已验证”的说法都不成立。

## 整改顺序

P0：端点绑定和 `iss`、Cookie 隔离、stdio 租户边界。  
P1：选定协议代际并补齐，按 annotation 区分副作用，统一 Chat 与 AgentRuntime。  
P2：取消、并发、错误码、标准密封。  
P3：指标、健康检查、是否改用官方 SDK。

详细条目在 `REMEDIATION_PLAN.md` 和 `ISSUES.md`。
