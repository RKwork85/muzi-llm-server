# OAuth 可信边界

实现在 `app/mcp/oauth_guard.py` 和 `app/mcp/service.py`。

## 可信 Issuer

目录是唯一锚点。`Connector.issuer` 有值时用它；否则用资源 URL 的 scheme 和 host，不带路径。这个值不从未经核对的元数据里读取。

校验顺序：

1. 锚点和资源 URL 先做端点形状与地址检查。
2. 受保护资源元数据的 `authorization_servers` 必须有一项与锚点精确相等。
3. 再按 RFC 8414 的路径形式请求授权服务器元数据。
4. 文档中的 `issuer` 必须与锚点精确相等。

`issuers_equal` 要求整段字符串相同，并且 scheme、netloc、path 都相同。用户信息、相似域名、多出来的路径、尾部斜杠和 `http`/`https` 差异都判为不相等。不用子串或前缀。

2026-11-08 读取的 Notion 公开元数据中，资源 `https://mcp.notion.com/mcp` 的授权服务器是 `https://mcp.notion.com`，文档 `issuer` 也是这个值。因此当前目录不写 `issuer` 时，Notion 仍能对上锚点。若以后授权服务器换成另一台主机，必须在 `connectors.toml` 写上那个 issuer，否则发现会失败关闭。

## 端点策略

Issuer 核对通过之后，授权、令牌和注册端点各自检查：

- 只允许 `https`。`mcp_allow_insecure=true` 时才允许 `http`。
- 拒绝用户名、密码和其他 scheme。
- 拒绝未指定、组播、链路本地、保留、私网、运营商级 NAT `100.64.0.0/10`、文档地址和基准测试网段。
- 回环地址只在 `mcp_allow_insecure=true` 时放行。生产默认关闭。
- 主机名要解析后再检查。字面 IP 直接检查。

进程内 ASGI 测试没有真实套接字，非字面主机可以跳过 DNS。生产客户端会解析。连接时 `_PinningBackend` 再次解析并检查，然后连到检查过的地址。TLS 的 SNI 仍使用原始主机名。这样可以挡住检查之后 DNS 改指向内网的情况。

带凭证的请求设置 `follow_redirects=False`。收到 3xx 就拒绝，不把授权码或 Refresh Token 转发到下一跳。

## 授权码和刷新

`begin` 把 verifier、issuer、token endpoint 和要求 `iss` 的标记封进现有 state 密文。`finish` 先核对一次性 state 和 PKCE，再按绑定的 issuer 检查 `iss`，然后重新检查 token endpoint，最后才提交授权码。请求不能另外指定 token endpoint。

刷新解密当前用户自己的连接记录。有绑定端点时只重查这个端点，不用新的元数据替换它。没有绑定的旧明文令牌只按当前严格发现走一次，成功后写成带绑定的密文。失败日志只记 connector 和原因码。

## RFC 9207

回调多读一个可选查询参数 `iss`。响应仍是原来的 302。

- 响应里有 `iss`：必须与本次 state 里的 issuer 精确相等，否则不交换令牌。
- 元数据 `authorization_response_iss_parameter_supported` 为 true 且响应没有 `iss`：拒绝。
- 该字段不是 true 且响应没有 `iss`：继续交换。这种情况不记成已经完成 RFC 9207 校验。

Notion 的公开元数据声明支持该参数。它的授权重定向需要带回匹配的 `iss`。
