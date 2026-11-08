# R1 测试记录

日期：2026-11-08。解释器：`C:\Users\rkwork\miniconda3\python.exe`。工作目录：`muzi-llm-server`。测试使用假 stdio 进程和进程内 ASGI 服务，没有真实 OAuth Token，也没有真实用户数据。

## 命令

```text
C:\Users\rkwork\miniconda3\python.exe -m pytest tests/agents/test_mcp.py tests/test_remote_mcp.py tests/test_mcp_r1_security.py tests/agents/test_tool_framework.py -q --tb=line
```

结果：31 passed，0 failed，耗时 1.48s。

| 文件 | 数量 | 结果 |
|---|---|---|
| `tests/agents/test_mcp.py` | 8 | 通过 |
| `tests/test_remote_mcp.py` | 5 | 通过 |
| `tests/test_mcp_r1_security.py` | 9 | 通过 |
| `tests/agents/test_tool_framework.py` | 9 | 通过 |

原有 MCP 测试是前两个文件，共 13 项，全部通过。新增安全测试 9 项全部通过。

另跑了 Agent API 里除 `test_deadline_cancel_and_disconnect` 以外的 6 项，6 passed。该项在本次修改前的 HEAD 代码上同样得到 `UPSTREAM_TIMEOUT`：模型超时是测试配置里的 5 秒，SSE 客户端要等终态事件，被阻塞的补全先超时。失败后测试没有放开阻塞线程，进程会停住。这不是 R1 引入的回归，本阶段没有改它。

## 新增测试覆盖

`tests/test_mcp_r1_security.py`：

1. `test_issuer_match_rejects_lookalikes`：相似域名、额外路径、尾斜杠、用户信息和 scheme 差异。
2. `test_endpoint_policy_blocks_unsafe_targets`：非 TLS、用户信息、私网、生产环境回环、不安全环境下的回环、错误 scheme、DNS 指向私网。
3. `test_malicious_metadata_does_not_receive_tokens`：Issuer 不在名单、私网 token endpoint、RFC 9207 `iss` 不匹配。这些情况下 token 命中数不增加。
4. `test_redirect_and_refresh_stay_on_the_bound_endpoint`：恶意元数据不能把刷新指到新地址；绑定端点返回 302 时只命中一次并且拒绝。
5. `test_cookie_jars_do_not_cross_users`：两个用户同一 Connector 的 Cookie、断开后的客户端清理。
6. `test_pinning_backend_connects_only_to_checked_address`：连接使用检查过的地址；解析到私网时拒绝。
7. `test_pool_clients_are_not_reused_across_users`：客户端身份、Cookie 隔离、关闭后的复用。
8. `test_stdio_grants_do_not_cross_users`：可见性、伪造名称、并发、撤销、子进程被杀死、重新注册后的授权。
9. `test_agent_stream_hides_stdio_from_other_users`：另一个用户的 agent 对话看不到对方的 stdio 工具。

正常元数据、正常授权码交换和正常刷新仍由 `tests/test_remote_mcp.py` 覆盖。假授权服务器现在返回文档 `issuer`。

## 日志

安全拒绝记 connector 和原因码，例如 `issuer_unlisted`、`issuer_mismatch`、`redirect`、`endpoint_address`。不记录 access token、refresh token、授权码、client secret、Cookie、PKCE verifier 或完整 Authorization 头。
