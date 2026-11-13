# 凭证加密

新密文使用 AES-256-GCM。格式是 `v2.{key_id}.{nonce}.{ciphertext}`，nonce 为 12 字节。附加认证数据是 `user_id|connector_id|credential_type`。

凭据类型包括 `access_token`、`refresh_token`、`pkce` 和 `client_secret`。客户端密钥不属于某个用户，绑定使用固定用户范围 `connector` 加上连接器编号，换到另一个连接器就不能解开。

标准库没有 AES-GCM，所以 `requirements.txt` 增加 `cryptography>=42.0.0`。当前解释器里已经能导入 `AESGCM`。

旧的 `TokenCipher` 保留为遗留格式。不以 `v2.` 开头的密文仍按旧算法解密，不因为升级而拒绝已有令牌。下次保存访问令牌、刷新令牌或验证码时写成 v2。加密失败不会保存，也不会把密文当令牌使用。

日志只记录原因，不记录明文、nonce 组合后的密钥或 Authorization 头。
