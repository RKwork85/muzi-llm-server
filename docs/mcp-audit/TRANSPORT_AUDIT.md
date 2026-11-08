# A04 Transport 审计

## STDIO

| ID | 项 | 等级 | 结果 | 证据 |
|---|---|---|---|---|
| A04-01 | stdout 帧解析 | MUST | PASS | 支持换行 JSON 和 `Content-Length`。`test_framed_stdio_round_trip`、`test_handshake_allowlist_env_and_reverse` |
| A04-02 | 日志不进 stdout | MUST | PASS | stderr 设为 `DEVNULL`。客户端日志走 `qs_llm` logger，不写子进程 stdout |
| A04-03 | 子进程退出与回收 | SHOULD | PARTIAL | `aclose` 能关闭和 kill。工具超时不结束子进程，测试明确断言进程对象不变 |
| A04-04 | 环境与工作目录 | SHOULD | PARTIAL | 环境被裁剪。没有设置独立 `cwd`，子进程继承服务进程工作目录 |
| A04-05 | 消息大小 | PROJECT | PASS | 单条上限 1_000_000 字节，超出抛 `mcp message invalid` |

## Streamable HTTP

| ID | 项 | 等级 | 结果 | 证据 |
|---|---|---|---|---|
| A04-06 | POST JSON-RPC | MUST | PASS | `_http` 对 `session.url` 发 POST。`test_oauth_refresh_selection_and_user_isolation` 走通 tools 调用 |
| A04-07 | Accept 与 Content-Type | MUST | PASS | `application/json, text/event-stream` 与 `application/json` |
| A04-08 | 202 与空 body | SHOULD | PASS | 状态 202 或空 body 时不等待响应体。假服务对通知返回 202 |
| A04-09 | SSE 响应体 | SHOULD | IMPLEMENTED_UNVERIFIED | `_parse_body` 能解析 `text/event-stream`。现有 HTTP 假服务返回 JSON，没有覆盖 POST 的 SSE 响应 |
| A04-10 | HTTP GET 服务端流 | MAY | NOT_IMPLEMENTED | streamable HTTP 分支没有单独的 GET SSE。GET 只用于 OAuth 探测和 legacy `sse` |
| A04-11 | 会话头 | MUST（旧传输）/ 不应再依赖（2026-07-28） | PARTIAL | 保存并回送 `mcp-session-id`。符合 2025-03-26 一代传输，不符合 2026-07-28 |
| A04-12 | 2026-07-28 路由头 | MUST | NOT_IMPLEMENTED | 无 `Mcp-Method`、`Mcp-Name`，也没有头和 body 的一致性校验 |
| A04-13 | HTTP 401 | PROJECT | PASS | 401 转为 `PermissionError`，服务层再标 `needs_reauth` |

## Legacy SSE

| ID | 项 | 等级 | 结果 | 证据 |
|---|---|---|---|---|
| A04-14 | 旧式 HTTP+SSE | MAY | PASS | 先 GET `text/event-stream`，读 `endpoint` 事件，再 POST 到该 URL，从 SSE 读 JSON-RPC。`test_sse_transport_calls_the_selected_tool` |
| A04-15 | 与 Streamable HTTP 区分 | PROJECT | PASS | `transport` 字段二选一，`_roundtrip` 分派到 `_sse` 或 `_http` |

Legacy SSE 应视为兼容层。2026-07-28 不应再把它当作当前传输。

## 传输安全

| ID | 项 | 等级 | 结果 | 证据 |
|---|---|---|---|---|
| A04-16 | 用户不能提交 MCP URL | PROJECT | PASS | 目录来自 `connectors.toml`。请求模型拒绝 `mcp_command` |
| A04-17 | 目录 URL 方案 | PROJECT | PASS | 默认只接受带 hostname 的 `https`。`test_cipher_hides_plaintext_and_catalog_rejects_http` |
| A04-18 | 重定向跟随 | SHOULD | PASS | 实测默认 `follow_redirects=False`，调用处也没有打开跟随 |
| A04-19 | TLS 校验 | MUST | PASS | 实测 `verify_mode=2`。代码没有 `verify=False` |
| A04-20 | OAuth 元数据目标限制 | MUST | FAIL | `_metadata`、`_json`、`_token`、动态注册使用元数据里的 URL。没有要求这些 URL 与目录主机、issuer 同源，也没有拒绝内网、链路本地和元数据服务地址。见安全审计 SEC-01 |
| A04-21 | DNS rebinding / Origin / Host | MUST（Server） | N/A | 这些是 MCP HTTP Server 的入站校验。本项目不是 MCP Server |
| A04-22 | 代理环境变量 | PROJECT | PASS | `app/main.py` 创建客户端时 `trust_env=False`。`mcp.py` 的 fallback 客户端同样关闭 |

传输层在“目录内 HTTPS + 不跟随重定向 + 校验 TLS”上有实际限制。授权发现阶段会离开这个限制，所以传输安全整体是 FAIL，不是 PASS。
