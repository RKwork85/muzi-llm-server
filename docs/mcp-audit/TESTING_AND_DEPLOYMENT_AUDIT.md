# A12 测试与部署审计

## 测试证据

2026-11-08 执行：

```text
C:\Users\rkwork\miniconda3\python.exe -m pytest tests/agents/test_mcp.py tests/test_remote_mcp.py -q --tb=line
13 passed in 1.28s
```

| 测试 | 覆盖 |
|---|---|
| `test_mcp_is_off_by_default` | 默认关闭，非法 server 名拒绝 |
| `test_request_cannot_carry_a_command` | 请求不能带 `mcp_command` |
| `test_registration_keeps_only_safe_allowlisted_tools` | allowlist、描述清洗、schema 裁剪 |
| `test_handshake_allowlist_env_and_reverse` | 握手、环境隔离、反向方法、缺参、未授权工具 |
| `test_framed_stdio_round_trip` | Content-Length 帧 |
| `test_unknown_process_and_timeout_do_not_restart` | 坏进程、缺失命令、超时不重启 |
| `test_failed_startup_still_serves_chat` | 启动失败不影响普通对话 |
| `test_ask_stream_calls_allowlisted_tool` | agent 调用、ask/plan 不暴露 |
| `test_cipher_hides_plaintext_and_catalog_rejects_http` | 密文和 HTTPS 目录 |
| `test_oauth_refresh_selection_and_user_isolation` | PKCE、刷新、选工具、用户隔离 |
| `test_sse_transport_calls_the_selected_tool` | 本机假 SSE |
| `test_connector_api_and_chat_stay_with_the_current_user` | API 401、回调不回显令牌、对话隔离 |
| `test_connect_requires_a_known_user` | 无用户不能 `require_user` |

## 缺口

| ID | 要求的测试 | 结果 |
|---|---|---|
| TST-01 | 协议版本不匹配、`server/discover`、`_meta` | NOT_IMPLEMENTED |
| TST-02 | JSON-RPC 错误码映射 | NOT_IMPLEMENTED |
| TST-03 | Resources、Prompts 正向调用 | N/A | 能力未实现。反向拒绝只在 stdio 反向测试中间接覆盖 |
| TST-04 | SSRF、issuer 绑定、RFC 9207 | NOT_IMPLEMENTED |
| TST-05 | 跨用户 Cookie | NOT_IMPLEMENTED |
| TST-06 | 路径穿越 | N/A | 无 Resources 读文件 |
| TST-07 | Prompt 注入回归 | PARTIAL | 只断言结果带 `untrusted` 和系统提示常量存在，没有对抗样本 |
| TST-08 | SQL 存储和 Alembic 迁移 | NOT_IMPLEMENTED | 本轮按审计边界没有执行迁移 |
| TST-09 | 真实 Notion 或真实 OAuth | 未执行 | 假服务不能证明生产服务器兼容 |
| TST-10 | AgentRuntime 任务路径 | NOT_IMPLEMENTED | 没有远程 MCP 用例 |
| TST-11 | 原有对话回归全量 | 未在本轮全量执行 | 只跑了两个 MCP 文件。MCP 启动失败用例证明普通补全仍返回“你好” |

## 可观测性

| ID | 项 | 结果 | 证据 |
|---|---|---|---|
| OBS-01 | request_id、工具名 | PARTIAL | `chat_service` 和 `ToolService` 记录 request_id 与工具名。MCP 层不记录 protocol、transport、latency |
| OBS-02 | 敏感头 | PASS | 检查过的 MCP 日志没有打印 Authorization 或 token。回调失败日志是固定句子 |
| OBS-03 | trace | NOT_IMPLEMENTED | 没有把一次对话连到 initialize、token 刷新和 tools/call 的链路 |
| OBS-04 | 指标 | NOT_IMPLEMENTED | 无调用量、成功率、P95、超时次数 |

## 部署

| ID | 项 | 结果 | 证据 |
|---|---|---|---|
| DEP-01 | 环境变量样例 | PASS | `.env.example` 列出 MCP 开关、命令、allowlist、token key、公开 URL |
| DEP-02 | 密钥默认空且不 repr | PARTIAL | `mcp_token_key` 的 Pydantic `repr=False`。生产没有“未配置密钥则拒绝启动”的检查；只有真正加密时才失败 |
| DEP-03 | SDK 锁定 | FAIL | 无官方 SDK，无锁文件。协议行为随自写代码变化 |
| DEP-04 | 健康检查包含 MCP | NOT_IMPLEMENTED | MCP 启动失败被吞掉，进程仍就绪。这避免了拖垮聊天，也使 MCP 故障不可从就绪状态看出来 |
| DEP-05 | 网络策略 | PARTIAL | 目录默认 HTTPS，客户端 `trust_env=False`。OAuth 元数据 URL 没有出口限制 |
| DEP-06 | 关闭 | PARTIAL | lifespan `finally` 关闭 `mcp_http` 和 stdio session。`mcp.py` 在缺少 `app.state.mcp_http` 时创建的 fallback 客户端没有关闭 |

现有测试能证明 happy path 和若干安全边界，不能证明协议最新版、OAuth 威胁模型或生产部署。
