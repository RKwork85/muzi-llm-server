# 协议回归

`tests/test_mcp_r2_protocol.py` 在本轮之后通过。支持的握手版本仍是 `2025-11-25`、`2025-06-18`、`2025-03-26`、`2024-11-05`。客户端优先发送 `2025-11-25`。`2026-07-28` 会拒绝并关闭会话。

工具分页、`notifications/tools/list_changed`、`ping`、标准 JSON-RPC 错误和会话 404 后的一次重新握手保持 R2.1 的行为。Resources、Prompts、Logging、Sampling、Roots、Elicitation、Completion、Tasks 没有实现，不能写成已支持。
