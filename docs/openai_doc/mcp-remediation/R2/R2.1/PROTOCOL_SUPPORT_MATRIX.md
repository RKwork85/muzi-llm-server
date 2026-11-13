# R2.1 协议支持矩阵

依据是官方仍使用 `initialize` 的生命周期规范，包括 `2025-11-25`、`2025-06-18`、`2025-03-26` 和 `2024-11-05`。官方当前版本 `2026-07-28` 已取消 `initialize` 和 `Mcp-Session-Id`。本客户端没有实现那套无状态请求，因此不声明支持。

客户端发出的首选版本是 `2025-11-25`。服务端若返回支持列表中的另一个版本，客户端改用该版本。返回列表之外的版本时，客户端断开会话。

客户端能力声明为 `{}`。不声明 roots、sampling、elicitation。

| 能力 | 状态 | 说明 |
|---|---|---|
| `initialize` / `notifications/initialized` | 支持 | 首个交互。版本不在支持列表内就断开 |
| `ping` | 支持 | 服务端发来的 `ping` 回复空结果 |
| `tools/list` | 支持 | 跟随官方 `cursor` / `nextCursor`，最多 8 页 |
| `tools/call` | 支持 | 接受文本内容。`isError: true` 视为工具失败 |
| `notifications/tools/list_changed` | 支持 | 收到后标记列表过期，下一次刷新重新拉取。不因此给用户新增授权 |
| `notifications/cancelled` | 支持 | 请求超时后发出，`initialize` 本身不取消 |
| `notifications/progress` | 忽略 | 不延长超时。总超时始终有效 |
| `notifications/message` 及其他通知 | 忽略 | 不使会话崩溃 |
| JSON-RPC 错误 `-32700` `-32600` `-32601` `-32602` `-32603` | 支持 | 只使用这些标准码。未知码记为 rpc 错误，不发明新码 |
| Streamable HTTP `MCP-Protocol-Version` | 支持 | 后续请求使用协商后的版本 |
| Streamable HTTP `Mcp-Session-Id` | 支持 | 仅对上述 legacy 版本。收到 404 时丢弃会话并重新握手一次 |
| SSE | 保留 | 仍作为同一套 JSON-RPC 的旧传输 |
| Resources | 不支持 | 服务端请求回复 `-32601` |
| Prompts | 不支持 | 服务端请求回复 `-32601` |
| Logging | 不支持 | 不声明，也不解析日志流 |
| Sampling | 不支持 | 服务端请求回复 `-32601` |
| Roots | 不支持 | 服务端请求回复 `-32601` |
| Elicitation | 不支持 | 服务端请求回复 `-32601` |
| Completion | 不支持 | 服务端请求回复 `-32601` |
| Tasks | 不支持 | `2025-11-25` 的任务能力未实现 |
| `2026-07-28` 无状态请求 | 不支持 | 无 `_meta` 协议版本，无 `server/discover` |

工具结果里的图片、音频和嵌入资源不会被当成已支持的资源能力。调用方目前只取文本内容。
