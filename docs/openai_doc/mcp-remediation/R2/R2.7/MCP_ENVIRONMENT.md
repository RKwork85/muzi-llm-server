# 密钥注入

生产进程只有同时满足下面的条件才会继续启动：

- `runtime_env=production`
- `mcp_key_source=external`
- `mcp_key_id` 不是 `local`、`dev`、`development`、`test` 或空值
- `MCP_TOKEN_KEY` 去掉空白后至少 32 个字符
- 密钥不是 `changeme`、`secret`、`password`、`mcp_token_key`、`test-token-key-012345`
- 轮换保留的旧密钥也满足同样的长度和编号限制

缺一项就拒绝启动。开发环境不套用这些限制，因此开发密钥编号 `local` 不能被生产进程接受。

密钥只从环境读取。源码、测试报告和日志都不写密钥正文。解密失败的错误是“密文无效”或“未知密钥”。

## 轮换

`rotate_sealed` 先用密文里的 key id 解密。解不开就停止，不写出新密文，避免把原来的凭证覆盖掉。解开以后用活动密钥重新加密，并立刻读回核对。

旧的 `TokenCipher` 密文仍然按原算法读取。新保存的凭证使用 `v2.{key_id}`。旧生产密钥可以留在 `MCP_PREVIOUS_TOKEN_KEYS`，直到确认没有密文再引用它，然后再从环境里删除。不要在还能解出旧密文之前撤掉旧密钥。

## 本次环境

没有把真实密钥注入到一个生产进程。这项是 BLOCKED。代码规则已由 `tests/test_mcp_r27_acceptance.py` 覆盖。
