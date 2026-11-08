# R2.1 实现说明

协议判断集中在 `app/mcp/protocol.py`。stdio 和远程 HTTP 都调用它，避免两套版本规则。

## 版本和握手

`client_initialize_params` 发送首选版本 `2025-11-25`、空能力和客户端名称。`accept_initialize_result` 只接受支持列表中的精确字符串。相似文本、空格和 `2026-07-28` 都拒绝。

拒绝发生在发送 `notifications/initialized` 之前。stdio 会关闭子进程。HTTP 会丢掉该用户和 Connector 的会话。之后的请求使用协商得到的版本填写 `MCP-Protocol-Version`。

## 工具、通知和错误

`tools/list` 按 `nextCursor` 继续请求，超过 8 页就停止并保留已得到的定义。这是客户端保护，不是协议字段。

收到 `notifications/tools/list_changed` 后，stdio 在下一次执行或显式刷新时重新拉列表。目录只收缩到新的允许名单，不把新工具自动授给已有用户。`set_catalog` 仍只在首次注册时清空授权。

超时后发送 `notifications/cancelled`，参数是 `requestId` 和 `reason: timeout`。`initialize` 超时不发送取消。迟到的响应因为请求已经移出等待表而被忽略。

服务端发来的 `ping` 返回 `{}`。其他带 id 的服务端请求返回 JSON-RPC `-32601`。通知没有 id，不回复。

工具调用的 JSON-RPC 错误变成 `ProtocolError`，stdio 进程和 HTTP 会话保持可用。版本不符、初始化失败、HTTP 404 和无法解析的响应会关闭当前会话。404 只重新握手一次。

## 没有改动的边界

R1 的 Issuer 检查、按用户分开的 HTTP 客户端，以及 stdio 的 `CURRENT_MCP_USER` 检查都还在。本阶段没有实现 R2.2 的统一执行入口，也没有把授权写入数据库。
