# 密文迁移

没有数据库结构变化。旧密文留在原来的文本列里。

读取时先看前缀。`v2.` 使用 AES-GCM 和附加认证数据。其他内容走原来的 `TokenCipher`。因此旧的访问令牌、刷新令牌和 PKCE 验证码仍然可以打开。

轮换时把旧材料放进 `MCP_PREVIOUS_TOKEN_KEYS`，新材料放进 `MCP_TOKEN_KEY` 并更换 `MCP_KEY_ID`。解密按密文里的 key id 找材料。新的保存使用活动密钥。找不到 key id 或认证数据不匹配时拒绝使用该凭证。
