# Phase 3 Tool Ecosystem 验收清单

每项必须填写实际结果，不允许预先勾选。每阶段结束附上真实的专项与全量测试数字。

## 开始前

- [ ] 当前路径是唯一项目根目录
- [ ] 已记录 git status / branch / log / diff --stat，用户修改已保护
- [ ] 已读取 ToolRegistry、ToolExecutor、ExecutionPolicy、AgentLoop、Provider 适配、Agent API、事件模型的真实实现
- [ ] 已重跑全量测试并记录真实结果（不直接采信 M3 报告的数字）
- [ ] 已核对 DeepSeek 官方文档中 function name/schema 约束与当前 SDK 版本

## T0 — 工具框架

- [ ] 元数据字段向后兼容，calculator/get_current_time 未改动即可注册
- [ ] write_local / write_external 注册被拒绝
- [ ] toolset 由服务端定义，请求只能按名选择
- [ ] 未知/不可用 toolset 返回新增错误码，不静默降级
- [ ] 不传 toolsets 时请求与响应和 M3 一致（有字段级比对测试）
- [ ] per-tool max_calls_per_run 生效且返回结构化失败
- [ ] 网络开关 TOOLS_NETWORK_ENABLED 默认 false 并有测试
- [ ] 网络工具独立并发闸门；排队时间计入 Deadline
- [ ] 同步线程池未被新工具占用
- [ ] Artifact 模型、数量与大小上限已定义并测试
- [ ] Phase 1 / M3 全部既有测试通过，未删除、未跳过、未放宽

## T1 — 本地工具与 Mermaid

- [ ] create_flowchart：同一输入逐字节一致输出
- [ ] 节点 key 为保留字/中文/emoji 时输出稳定（内部 ID）
- [ ] 标签中的引号、尖括号、&、|、;、#、换行、超长文本被转义或拒绝
- [ ] 节点/边/标签/源码长度上限生效，边引用错误被拒
- [ ] 输出不含 click/href/style/classDef/init 指令/frontmatter
- [ ] create_sequence_diagram 同样通过上述检查
- [ ] validate_mermaid 覆盖全部拒绝项；guarantees_render 恒为 false
- [ ] 工具描述明确"不保证可渲染"，没有向模型暗示保证
- [ ] artifacts 通过 API 返回；事件流不含 artifact 正文
- [ ] 默认请求看不到、调用不了这些工具；伪造调用被拒并回传失败
- [ ] E2E（mock Provider）：请求 → create_flowchart → tool message → 最终答复 → artifacts
- [ ] format_json、text_stats、date_diff 有边界测试，无副作用

## T2 — 联网工具（DeepSeek Search / Fetch）

- [ ] 已重新对照官方三页文档（或如实标注未对照），差异已写入 DEVIATIONS.md
- [ ] 复用现有 DeepSeek Key，无新增搜索 Key；无 Key 或网络关闭时 research toolset 不可用且不崩溃
- [ ] httpx 客户端由 lifespan 管理，follow_redirects=False，trust_env 已明确
- [ ] 请求体断言：Search 含 need_content=false、count、无 localization；Fetch 只含 url
- [ ] Authorization 头存在于请求，且不出现在日志、异常、工具结果、事件
- [ ] 响应字节上限生效；未知字段忽略；缺必填字段/非 JSON 安全失败
- [ ] 状态码映射 400/401/402/403/404/408/413/429/500/504 逐条通过，文案不含上游原文
- [ ] 默认不重试；开启时仅限允许的状态码且不突破 Deadline
- [ ] web_search 返回 summary 等精简字段，空结果为成功，非 http(s) URL 被丢弃
- [ ] URL 预校验：scheme、凭据、空 host、超长、端口、内部主机名、IP 各种写法与 IPv6、凭据类 query 参数
- [ ] URL 来源约束（provenance 默认）：未出现过的 URL 被拒；用户 URL 与搜索结果 URL 通过；正文中新链接被拒；规范化等价；跨运行不串
- [ ] web_fetch 结果不含编造的 final_url/status/content_type；截断带 truncated/original_chars
- [ ] 外部内容 untrusted 标注；Prompt 条款已加入
- [ ] 注入测试：诱导调用未授权工具、诱导抓取未出现过的 URL，均被拒并回传失败
- [ ] 次数上限、并发闸门、超时、取消均有测试
- [ ] SECURITY_REVIEW 写明依赖第三方的假设与 `any` 策略风险
- [ ] 默认测试不访问互联网；WEB_LIVE 默认 skip，执行与否如实记录

## T3 — 文件工具

- [ ] `..`、绝对路径、盘符、UNC、`:`、保留设备名、超长路径被拒
- [ ] 使用 is_relative_to，`root` 与 `root_evil` 绕过被挡
- [ ] 符号链接/联接逃逸被拒（或如实标注等价单测）
- [ ] 敏感文件、隐藏路径、.git/.venv 被拒，且与"不存在"外部表现一致
- [ ] 二进制/非 UTF-8/超大文件被拒
- [ ] 条目数、匹配数、扫描文件数、字节数上限生效
- [ ] search_text 为字面量搜索，不接受正则
- [ ] 无写入、删除、移动、执行能力

## T4/T5 — 设计评审

- [ ] docs/phase3/T4_T5_DESIGN.md 已交付
- [ ] 未实现任何业务 API、数据库或 MCP 代码
- [ ] T5 含"MCP 补充阶段测试方案"：Everything 测试服务、工具名不写死、仅 tools、命令只来自配置、不传密钥给子进程、命名空间与白名单、独立 toolset 默认关闭、子进程生命周期、离线假服务测试、真实服务测试需授权
- [ ] 未安装 Node.js 或任何 MCP 服务，未引入 MCP SDK
- [ ] 已列出需要用户决定的问题，并停止等待

## T6 — 加固与验证

- [ ] SECURITY_REVIEW.md 完成
- [ ] README、.env.example（无密钥）、API 示例已更新
- [ ] 能力归属实测：对照组/实验组各 ≥ 3 次，真实记录；未执行则写"未执行"
- [ ] Phase 1 / M3 兼容性：路由、Schema、错误码、SSE 事件名
- [ ] 全量测试通过；skip 原因真实透明
- [ ] git diff --check 通过

## 最终

- [ ] 没有未解释的失败测试
- [ ] 变更清单准确，DEVIATIONS.md 完整（无偏差写 none）
- [ ] 已知限制列出（含任务不跨进程、同步线程不能强杀等既有限制）
- [ ] 未擅自提交、推送或改 Tag
- [ ] 未夸大 live 验证范围
