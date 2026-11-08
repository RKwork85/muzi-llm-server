# 密钥管理

`KeyRing` 是当前的密钥接口。活动密钥来自 `MCP_TOKEN_KEY`，活动编号来自 `MCP_KEY_ID`，默认 `local`。旧密钥写在 `MCP_PREVIOUS_TOKEN_KEYS`，格式为 `key_id:secret`，多项用逗号分隔。材料经 SHA-256 收成 32 字节，源码和数据库都不存放密钥。

`runtime_env` 默认 `development`，避免现有测试因为没有生产密钥而无法启动。`runtime_env=production` 时，启动调用 `validate_key_config`：

- `mcp_key_source` 必须是 `external`。
- 密钥去掉空白后至少 32 个字符。
- 拒绝 `changeme`、`secret`、`password`、`mcp_token_key` 这类占位值。

这里的 `external` 表示密钥由环境或外部秘密管理系统注入。进程内还没有接某个具体云厂商的 KMS 客户端；接入时替换 `KeyRing` 的材料来源，不改变密文格式。
