# A08 / A09 安全审计

官方依据：`https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization`（2026-11-08）。其中与本实现直接相关的强制要求包括：用 Protected Resource Metadata 发现授权服务器、授权码换 token 前按 RFC 9207 校验 `iss`、`resource` 参数、Bearer 头、刷新令牌保密，以及不得把令牌送到该 MCP 服务器的授权服务器之外。

## 认证

| ID | 项 | 等级 | 结果 | 证据 |
|---|---|---|---|---|
| SEC-01 | 授权端点必须绑定已校验的 issuer | MUST | FAIL | `McpService._metadata` 取 `authorization_servers[0]`，再请求 `{issuer}/.well-known/oauth-authorization-server`，随后向文档中的 `authorization_endpoint`、`token_endpoint`、`registration_endpoint` 发请求。这三个 URL 没有与 issuer 或目录主机比对，也没有拒绝私网地址。授权码和 refresh token 会 POST 到元数据指定的地址 |
| SEC-02 | Protected Resource Metadata | MUST | PASS | `_pointed_metadata` 解析 `WWW-Authenticate` 的 `resource_metadata`，失败再退回 `/.well-known/oauth-protected-resource`。测试断言探测路径是 `/meta` |
| SEC-03 | RFC 8707 `resource` | MUST | PASS | `begin`、授权码换票和刷新都带 `resource=connector.url` |
| SEC-04 | PKCE S256 | MUST | PASS | `begin` 生成 verifier 和 challenge。假 token 端点校验 challenge。测试能完成授权 |
| SEC-05 | state 一次性 | MUST | PASS | 保存的是 state 哈希，`pop_state` 取出后删除。过期返回空。回调错误会回到 `connected=failed`，Location 不含 access token |
| SEC-06 | RFC 9207 `iss` | MUST | FAIL | 回调只声明 `code` 和 `state`。不记录 metadata 的 issuer，也不比较授权响应里的 `iss`。`finish` 在校验前就把 code 送到 token 端点 |
| SEC-07 | scope 与 step-up | SHOULD | NOT_IMPLEMENTED | 授权请求没有 scope。不读取 `WWW-Authenticate` 的 scope，也没有 step-up |
| SEC-08 | Client ID Metadata Documents | SHOULD | NOT_IMPLEMENTED | 只实现 Dynamic Client Registration。2026-07-28 将 DCR 标为向后兼容，主路径是 Client ID Metadata Documents |
| SEC-09 | Bearer 使用 | MUST | PASS | `Authorization: Bearer` 放在头里，不放进 URL。401 会把连接标成 `needs_reauth` |
| SEC-10 | 令牌存储 | MUST | PARTIAL | 数据库和内存记录保存密文。测试断言明文 access token 不在密文和 store repr 中。加密是 SHA-256 流与 HMAC 共用同一把 `SHA-256(MCP_TOKEN_KEY)`，不是 AES-GCM 或其他标准 AEAD。密钥短于 16 字符时 `cipher()` 拒绝工作 |
| SEC-11 | 令牌不进日志和模型上下文 | MUST | PARTIAL | 应用日志只写 connector 名或“oauth callback failed”。审计表脱敏不覆盖 MCP 表，MCP 表本身存的是密文。工具结果测试不含父进程 API Key。远程工具文本若含令牌，仍会作为不可信结果进入模型；代码没有专门扫描 |
| SEC-12 | stdio 凭证 | PROJECT | PASS | 子进程环境不含父进程密钥。测试用 `DEEPSEEK_API_KEY` 验证未泄漏。没有单独的 stdio OAuth |

## 授权与租户

| ID | 项 | 等级 | 结果 | 证据 |
|---|---|---|---|---|
| SEC-13 | 用户连接隔离 | MUST | PASS | `get_connection` / `list_connections` 按 `user_id`。唯一约束 `(user_id, connector_name)`。测试中 user-a 有 `mcp__demo__echo`，user-b 为空 |
| SEC-14 | 未登录不能连接 | MUST | PASS | `test_connector_api_and_chat_stay_with_the_current_user`：无 Bearer 时 connect 返回 401 |
| SEC-15 | 对话只挂当前用户的远程工具 | PROJECT | PASS | `chat.py` 仅在 `mode==agent` 且能解析出 user id 时调用 `tool_specs`。同测试里 token-b 的后续对话看不到 `mcp__demo__echo` |
| SEC-16 | ask / plan 不执行 MCP | PROJECT | PASS | `prepare_stream` 在非 agent 模式清空 remote。`ask_tool_names` 不含 MCP。`test_ask_stream_calls_allowlisted_tool` 断言 ask 和 plan 的工具列表没有 `mcp__fake__echo` |
| SEC-17 | 共享 HTTP 客户端的 Cookie | MUST | FAIL | `app/main.py` 把一个 `httpx.AsyncClient` 放在 `app.state.mcp_http`，所有用户共用。httpx 默认 Cookie 罐会保存 `Set-Cookie` 并在后续同主机请求带回。这会造成用户间会话混合。现有测试没有覆盖 |
| SEC-18 | 共享 stdio 进程 | PROJECT | FAIL | `register_mcp_tools` 在启动时写入全局 agent registry。`agent_chat_tool_names` 对所有 agent 对话暴露低风险 MCP 工具，没有按用户过滤。多用户部署下，一个本地 MCP 进程的权限就是所有用户的权限 |
| SEC-19 | 工具调用仍受执行器约束 | PROJECT | PARTIAL | 远程和 stdio 都经 `ToolService.execute` 的 allowlist、超时和结果截断。它们不进入 `app/agents/executor.py` 的 AgentRuntime 策略。stdio 名字不在用户授权列表里也会随 agent 模式出现 |
| SEC-20 | MCP 认证通过不等于全部工具可调用 | PROJECT | PASS | 远程必须再经 `selected_tools`。stdio 必须再经 `mcp_tool_allowlist`。测试拒绝未列入的 `secret` |
| SEC-21 | 写入工具误分类 | PROJECT | FAIL | 见 A05-11。未命中正则的副作用工具会以 `read_network` 且 `idempotent=True` 注册。`connectors.toml` 里 Notion 还打开了 `allow_write`，命中正则的工具可以变成 `mutate_remote` |
| SEC-22 | Confused deputy / token passthrough | MUST | FAIL | 代码没有把访问令牌转给模型，这点是好的。但 SEC-01 会把授权码和 refresh token 交给元数据中的任意 token 端点，这就是跨服务转发。没有 audience 绑定的二次确认 |
| SEC-23 | 目录接口匿名可见 | SHOULD | PARTIAL | `list_connectors` 使用 `user_id_from_request`，不调用 `require_user`。无登录时仍返回目录名称、传输和 `allow_write`，连接状态为 disconnected |

## 一票否决核对

| 否决项 | 是否成立 | 依据 |
|---|---|---|
| 未授权工具执行 | 不单独成立 | 远程工具要登录、agent 模式和用户勾选。stdio 工具要服务端开关和 allowlist。缺口是多用户共享，不是匿名直接调用 |
| 跨租户数据泄露 | 成立 | SEC-17 共用 Cookie 罐。SEC-18 共用 stdio 进程和它能接触的数据 |
| Token 泄露或跨服务错误转发 | 成立 | SEC-01 未校验 issuer 和 token 端点就提交授权码、refresh token、client secret |
| 严重 SSRF | 成立 | 同一路径让服务端去请求元数据给出的任意 URL，包括内网地址。用户不能直接填 MCP URL，但不能阻止已配置服务器的元数据把客户端带到别的地址 |
| 关键权限绕过 | 部分成立，不单独作为唯一否决 | 写入判断可被工具名绕过。仍需用户把该工具选进 `selected_tools`，或 stdio allowlist 包含它 |
| 不可控高危副作用 | 部分成立 | Notion 目录允许写入；副作用工具可被标成幂等只读。有用户勾选和 agent 模式两道门，没有逐次确认 |

只要上表有一项成立，生产结论就不能是合格。这里有三项成立。

## 已做到的控制

这些控制有测试，不能被否决项抹掉：

- 默认关闭 MCP。
- 请求不能自带命令或服务器地址。
- PKCE、state 哈希、令牌密文、按用户选工具。
- ask 和 plan 不挂 MCP。
- 工具结果标记不可信，并有一段系统提示要求不当成指令。
- TLS 校验开启，HTTP 重定向默认不跟随。
