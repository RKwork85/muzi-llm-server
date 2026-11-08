# R2.1 测试记录

日期：2026-11-08。解释器：`C:\Users\rkwork\miniconda3\python.exe`。工作目录：`muzi-llm-server`。

## 命令

```text
C:\Users\rkwork\miniconda3\python.exe -m pytest tests/test_mcp_r2_protocol.py tests/agents/test_mcp.py tests/test_remote_mcp.py tests/test_mcp_r1_security.py tests/agents/test_tool_framework.py -q --tb=line
```

结果：37 passed，0 failed，耗时 2.97s。

| 文件 | 数量 | 结果 |
|---|---|---|
| `tests/test_mcp_r2_protocol.py` | 6 | 通过。本阶段新增 |
| `tests/agents/test_mcp.py` | 8 | 通过。原有 stdio |
| `tests/test_remote_mcp.py` | 5 | 通过。原有远程 MCP |
| `tests/test_mcp_r1_security.py` | 9 | 通过。R1 安全回归 |
| `tests/agents/test_tool_framework.py` | 9 | 通过。非 MCP 工具回归 |

R1 的 13 个 MCP 测试和 9 个安全测试都在这 37 项里，没有失败。

## 新增覆盖

1. 支持列表拒绝 `2026-07-28` 和相似版本字符串。
2. stdio 接受服务端回显的首选版本，并在 `tools/list` 期间回复 `ping`。
3. stdio 遇到 `2026-07-28` 时关闭，不保持可用。
4. `tools/list` 跟随 `nextCursor`。
5. `notifications/tools/list_changed` 触发刷新，原用户授权仍在，另一用户仍为空。
6. 超时发送 `notifications/cancelled`，不重启 stdio 进程。
7. JSON-RPC `-32602` 之后同一会话仍能调用。
8. HTTP 协商后把 `MCP-Protocol-Version` 改成服务端版本，保存 `Mcp-Session-Id`。
9. 不支持的 HTTP 版本会删除会话。
10. 旧会话 404 后重新握手一次。
11. 同一条 SSE 响应里的列表变更通知和结果可以同时处理。
12. HTTP 超时发送取消通知，并且不把该会话当成永久失效。
