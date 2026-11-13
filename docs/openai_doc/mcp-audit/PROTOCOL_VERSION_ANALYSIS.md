# 协议版本分析

官方文档读取时间：2026-11-08。

| 来源 | 版本 |
|---|---|
| 代码里客户端发出的 `protocolVersion` | 固定 `2024-11-05`。`app/tools/mcp_client.py` 第 82 行，`app/mcp/http_client.py` 第 156 行 |
| HTTP `MCP-Protocol-Version` 初始值 | `RemoteSession.protocol` 默认 `2024-11-05` |
| 服务端返回值 | HTTP 客户端把 initialize 结果里的 `protocolVersion` 原样写入 `session.protocol`，没有允许列表 |
| stdio 客户端 | 不读取、不校验服务端返回的 `protocolVersion` |
| 项目依赖声明 | 无 SDK，无协议版本锁定 |
| 官方当前规范 | `2026-07-28`。该版起协议无状态：取消 `initialize` / `notifications/initialized` 和 `Mcp-Session-Id`；每个请求在 `_meta` 携带 `io.modelcontextprotocol/protocolVersion` 与 `clientCapabilities`；HTTP 同时使用 `MCP-Protocol-Version`，并增加 `Mcp-Method`、`Mcp-Name` |
| 官方对旧版的称呼 | `2025-11-25` 及更早为 Legacy，依赖 initialize 握手 |

## 协商机制

项目没有版本协商。

- 不发送 `2026-07-28` 的 per-request `_meta`。
- 不调用 `server/discover`。
- 不处理 `UnsupportedProtocolVersionError`（官方错误码 `-32022`）。
- 不在版本不支持时断开。2024-11-05 及后续 legacy 规范要求客户端拒绝自己不支持的 `protocolVersion`。
- HTTP 客户端会把不认识的服务端版本继续放到 `MCP-Protocol-Version` 头里。

因此：

1. **当前实现版本符合性**：按 `2024-11-05` 的 tools 子集评估，为部分符合。握手、JSON-RPC 和 tools 调用是真的，版本校验和若干 MUST 行为缺失。
2. **2025-03-26 / 2025-06-18 Streamable HTTP**：有 POST、Accept、Session Id 和 SSE 响应体解析，仍走旧握手。不能称为该传输的完整实现。
3. **2026-07-28 兼容性**：不符合。只支持 legacy 握手的服务端可以继续被这个客户端调用；只支持 2026-07-28 的服务端不能被正确调用。
4. **废弃能力**：Legacy SSE 被标成传输 `sse` 并实际实现。`Mcp-Session-Id` 和 Dynamic Client Registration 在新规范中已退出主路径。代码没有把它们标成兼容层。
5. **升级内容**：若目标是最新规范，需要无状态请求、`_meta`、`server/discover`、头字段一致性、RFC 9207 `iss` 校验，以及 Client ID Metadata Documents 优先于 DCR。若目标仍是 `2024-11-05`，至少要校验返回的协议版本，并在文档中明确不支持 2026-07-28。

不能把“仍使用 initialize”直接判成 2024-11-05 不合格。也不能把这套实现写成已经符合 2026-07-28。
