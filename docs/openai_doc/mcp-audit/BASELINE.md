# M0 项目基线

审计日期：2026-11-08。审计对象是工作区中的当前代码，不是已提交历史。

## 仓库

| 项 | 证据 |
|---|---|
| 项目根 | `C:\Users\rkwork\Desktop\muzihhh\work\muzi_project\muzi-llm\muzi-llm-server` |
| 分支 | `main` |
| HEAD | `8e123d1`（`version: muzi-llm-server v1.0.0`） |
| 工作区 | 审计开始时已有未提交修改和未跟踪文件。本轮没有回滚、覆盖或提交这些文件。 |

MCP 实现主要在未跟踪文件中，HEAD 提交本身不包含这套远程 MCP。结论针对工作区现状。

## 依赖

`requirements.txt` 有 FastAPI、httpx、Pydantic、SQLAlchemy、Alembic。没有 `mcp` 或 `modelcontextprotocol` 包，也没有锁文件。

2026-11-08 用 `C:\Users\rkwork\miniconda3\python.exe` 检查：`importlib.util.find_spec("mcp")` 为 `None`。实现是自写 JSON-RPC 客户端，不是官方 Python SDK。

同一次检查：`httpx.AsyncClient` 的 `follow_redirects` 为 `False`，新建客户端的 Cookie 罐为空但会保留后续 `Set-Cookie`，TLS `verify_mode` 为 `2`（`CERT_REQUIRED`）。

## 配置

`app/core/config.py` 中 MCP 默认关闭：

- `mcp_enabled=false`
- `mcp_command` 为空
- `mcp_tool_allowlist` 为空
- `mcp_timeout_seconds=10`，上限 30
- `mcp_token_key` 为空
- `mcp_allow_insecure=false`
- `mcp_public_base_url` / `mcp_frontend_base_url` 指向本机

`.env.example` 第 65–76 行与上述默认值一致。开启 stdio 时，`MCP_SERVER_NAME` 必须匹配 `[A-Za-z0-9_]{1,16}`。

目录文件 `app/mcp/connectors.toml` 只登记了 Notion：`https://mcp.notion.com/mcp`，`streamable_http`，`allow_write=true`。

## 测试

命令：

```text
C:\Users\rkwork\miniconda3\python.exe -m pytest tests/agents/test_mcp.py tests/test_remote_mcp.py -q --tb=line
```

结果：`13 passed in 1.28s`。全部使用假进程或假 HTTP 服务，没有访问 Notion，也没有真实 OAuth 服务。

## 审计范围

纳入：

- stdio 客户端 `app/tools/mcp_client.py`
- 远程客户端 `app/mcp/`
- 连接管理 API `app/api/v1/endpoints/mcp.py`
- 对话接入 `app/api/v1/endpoints/chat.py`、`app/services/chat_service.py`
- 启动接入 `app/main.py`
- 表结构和迁移
- 上述测试

不纳入为“已实现的 MCP Server”：仓库里没有对外提供 MCP JSON-RPC 的服务端。FastAPI 的 `/api/v1/mcp/*` 是本应用自己的连接管理接口。

## 验证限制

- 未对 `https://mcp.notion.com/mcp` 做真实握手。
- 未使用真实授权服务器验证 RFC 9207 或 Client ID Metadata Documents。
- 未做多 worker、多副本部署实测。
- 官方规范于 2026-11-08 读取：`https://modelcontextprotocol.io/specification/2026-07-28` 及其 versioning、authorization 页面。
