# 观测

项目里没有 Prometheus。计数和耗时放在进程内的 `app/mcp/observe.py`，不新增监控依赖。

计数名称：

- `mcp_connection_total`
- `mcp_connection_failures_total`
- `mcp_tool_calls_total`
- `mcp_tool_call_duration`（耗时样本，单位秒）
- `mcp_tool_errors_total`
- `mcp_auth_denied_total`
- `mcp_token_refresh_failures_total`

日志事件包括工具完成、超时、取消、重试、失败和拒绝。字段有用户、连接器、工具、请求、对话、运行编号和状态。禁止出现在字段名里的词包括 token、secret、cookie、password、authorization、verifier、code。正文里的 `Bearer ` 和 `cookie=` 会替换掉。

这些计数随进程重启清零，不能代替外部监控系统。
