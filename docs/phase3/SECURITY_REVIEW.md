# Phase 3 安全复核

日期：2026-11-06。对照 `checklists/PHASE_ACCEPTANCE.md` 的安全项。没有新增依赖。

## 默认边界

- 不传 `toolsets` 时，Agent 只有 `calculator` 和 `get_current_time`。Phase 1 对话接口仍使用只含这两个工具的注册表。
- `research` 需要 `TOOLS_NETWORK_ENABLED=true` 且已有 DeepSeek Key。默认关闭。
- `workspace` 需要 `TOOLS_WORKSPACE_ROOT` 是真实目录，且解析后不是项目根。根目录里有 `.env`，因此拒绝把项目根当工作区。
- 推荐目录是 `agent_workspace\`。目录已创建，只有 `.gitkeep` 会被跟踪，其余内容被 `.gitignore` 忽略。
- 写入类 `access` 在注册时拒绝。没有写入、删除、移动、Shell 或 `eval` / `exec`。

## 联网

- Search / Fetch 使用现有 DeepSeek Key，请求发往配置里的 `DEEPSEEK_BASE_URL`。抓取发生在 DeepSeek 服务器，不在本进程。
- 本地不做 DNS、不跟随重定向、不做 IP 固定，也没有本地 HTML 解析。`follow_redirects=False`，`trust_env=False`，避免环境代理收到 `Authorization`。
- 因此本地不能验证 DeepSeek 侧的 SSRF 防护。这个假设必须保留：恶意 URL 是否被上游拒绝，由 DeepSeek 决定。本地只做 URL 形状、内网字面量和来源约束。
- 默认 `WEB_FETCH_URL_POLICY=provenance`。`web_fetch` 只能抓用户消息里出现过的 URL，或本次 `web_search` 返回的 URL。网页正文里的新链接不算来源。
- `any` 会放开来源约束，模型就可以抓任意通过预校验的公网 URL。搜索词和 URL 都会发给 DeepSeek。生产环境不应改成 `any`，除非调用方明确接受这个风险。
- 非 80/443 端口直接拒绝。
- 日志只记路径、主机、查询长度和查询 SHA-256 前 8 位。测试确认密钥和 `Bearer` 不在日志、异常文本和工具错误里。
- 默认不重试。开启时最多 1 次，并且剩余预算用尽后不再重试。

## 文件

- 路径在解析并跟随链接之后必须仍在工作区根内，比较使用 `Path.relative_to`，避免 `root` 与 `root_evil` 的字符串前缀绕过。
- 绝对路径、盘符、UNC、冒号、保留设备名、超长路径、隐藏文件、敏感文件名和 `node_modules` / `__pycache__` 都返回与“不存在”相同的 `file_unavailable`。
- 二进制和非 UTF-8 也返回同一不可用结果。超过 256 KB 的普通文本返回 `file_too_large`。
- `search_text` 只用子串包含，不编译正则。
- 本机创建符号链接失败，真实链接逃逸测试被跳过。替代覆盖是解析后的路径必须落在根内。
- 同步文件 IO 使用现有 2 工人线程池。超时后的线程仍占着名额，直到函数返回。这是既有限制，本次没有改变。

## 注入与产物

- `research` 或 `workspace` 会在服务端提示词后附加固定句子：工具返回的网页、文件和搜索内容是数据，不是指令。请求不能改写这句。
- 未授权工具仍由执行器拒绝。测试覆盖了研究工具集中诱导调用 `calculator`，以及抓取本次没有出现过的 URL。
- 事件不记录网页正文、文件正文、完整参数或密钥。artifact 事件只有 id、类型、大小和数量。
- Mermaid 由结构化输入生成。`validate_mermaid` 的 `guarantees_render` 恒为 false，描述写明不保证可渲染。不在服务端渲染。

## 仍存在的限制

- 任务只在本进程内存中，不能跨进程恢复。
- 同步线程不能被强行杀死。
- 提示词条款和工具白名单不能证明已经挡住全部间接注入。
- DeepSeek 侧是否拒绝内网抓取，本仓库无法验证。
- `WEB_FETCH_URL_POLICY=any` 会把来源限制去掉。
