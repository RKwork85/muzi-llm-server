# stdio 进程与用户授权

实现在 `app/tools/stdio_access.py`、`app/tools/mcp_client.py`、`app/agents/toolsets.py`、`app/services/chat_service.py` 和 `app/agents/runtime.py`。

## 四件事分开

| 对象 | 是否共享 | 原因 |
|---|---|---|
| stdio 子进程 | 应用内一份 | 启动参数来自服务端配置。环境只有 `PATH` 和 `SYSTEMROOT`，没有用户凭证 |
| MCP 会话 | 跟着这一个进程 | 进程本身不保存用户身份 |
| 工具定义 | 可以共享 | 仍注册在现有 ToolRegistry，名称形如 `mcp__{server}__{tool}` |
| 执行权限 | 按用户 | `StdioAccess` 和调用时的当前用户 |

请求不能带命令、脚本路径或任意服务地址。子进程仍只从受信任的服务端配置启动。

## 授权

`set_catalog` 在注册工具时写入允许出现的本地名称，并清空已有授权。这样重新注册不会留下上一个进程的用户授权。

`ensure(user)` 只在该用户还没有记录时，复制一份当前目录。已有的 `grant` 或 `revoke` 不会被盖掉。`revoke` 把该用户设成空集。

对话的 agent 模式在确认用户后调用 `ensure`。工具名单来自 `agent_chat_tool_names`，只加入该用户授权内、风险为低的 MCP 工具。这些工具本来就在 Registry 里，不再叠进远程工具层，避免同名重复注册。

未登录用户没有 stdio 工具。Ask 和 Plan 不进入这条路径。

## 执行

stdio handler 读取 `CURRENT_MCP_USER`。没有用户、用户不在授权里，或工具不在目录里，抛出 `PermissionError`，由现有 ToolService 变成工具执行失败。伪造的工具名到不了 handler，Registry 会给出 `ToolNotAllowedError`。

Chat 流在执行期间设置这个 ContextVar。AgentRuntime 的 `start` 用同一个用户过滤 `agent_tool_names`，`_execute` 在整个任务里设置并在结束时复位。Agent 接口只在已有 MCP 服务时解析用户，响应结构不变。

## 重启

授权在内存中。进程重新注册后，旧授权消失，不会被另一个用户继承。下一次已登录对话会通过 `ensure` 得到目录副本，也就是服务端 allowlist 的一份，不是别人的显式授权。

更窄的显式授权不会跨进程保留。把它写入数据库会改变存储，本阶段没有做。这不是跨用户继承，而是重启后回到目录策略。
