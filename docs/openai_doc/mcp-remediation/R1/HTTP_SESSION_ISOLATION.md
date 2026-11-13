# HTTP 会话隔离

实现在 `app/mcp/pool.py`、`app/mcp/http_client.py` 和 `app/mcp/service.py`。

## 隔离维度

客户端键是用户 ID 加 Connector 名称。只按 Connector 建一个全局客户端的做法已经去掉。应用启动时创建空的 `ClientPool`，不再把一个带 Cookie 的 `httpx.AsyncClient` 放在 `app.state` 上给所有用户共用。

同一个键会复用未关闭的客户端。键不存在、客户端已关闭，或 `discard` 之后，下次请求再创建。`aclose` 关闭池里剩下的客户端。

## Cookie 和 Token

每个客户端构造时使用新的 `httpx.Cookies()`。用户 A 的 `Set-Cookie` 写在 A 的客户端上，B 的请求看不到。两边同时更新也不会写进对方的罐。

OAuth access token 和 refresh token 仍在该用户的连接记录密文里。RPC 从这条记录取出当前用户的 access token，放进这次请求的 Authorization 头。Cookie 不承担这个职责，Token 也不写入 Cookie 罐。

`disconnect` 删除该用户这条连接，并丢弃对应客户端和 RemoteHub 会话。其他用户的客户端保留。

## 生命周期和并发

创建、复用、关闭、丢弃和断开后的清理都由池完成。测试用的 ASGI 传输会克隆应用和 base URL，并带上新的 Cookie 罐，避免每次请求新建后不关闭，也避免回到全局客户端。

`RemoteHub.call` 的锁键同样是用户加 Connector。它只串行这一个用户在这一个 Connector 上的会话创建和 JSON-RPC。别的用户使用自己的客户端和锁。Token 刷新在进入 RPC 前使用池上的同一维度锁，刷新后丢掉旧客户端，避免把旧会话留给下一次调用。

日志不记录 Token、Authorization 头或 Cookie。失败只记 connector 和原因码。
