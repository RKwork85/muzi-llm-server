# Cursor 启动指令 — Phase 3 Tool Ecosystem

你是本项目的资深 Python/FastAPI Agent Runtime 工程师。请执行 Phase 3 工具生态扩展任务。

【路径】
唯一项目根目录：
C:\Users\rkwork\Desktop\muzihhh\work\muzi_project\qs_llm_dp

Skill 目录：
C:\Users\rkwork\Desktop\muzihhh\work\muzi_project\qs_llm_dp\phase-skills\skill3_deepseek_fastapi_agent_runtime_plus

不要访问任何旧项目路径，不要另建同功能的 Skill 目录。

【我已做出的决定（如需更改我会明确告诉你）】
1. 搜索与抓取使用 DeepSeek 官方 Search / Fetch 接口，与对话接口共用同一个 DeepSeek API Key（本机 .env 已有），不新增搜索 Key。抓取由 DeepSeek 服务器执行，不是本项目后端。
2. `WEB_FETCH_URL_POLICY` 默认 `provenance`：web_fetch 只能抓取用户消息中出现过的 URL，或本次运行 web_search 返回的 URL。
3. 重试默认关闭（`WEB_TOOLS_MAX_RETRIES=0`）。
4. T3 文件工具的根目录使用项目内专用目录 `agent_workspace\`（由你创建，只放 `.gitkeep`，内容加入 .gitignore）。**绝不能把项目根目录当作工作区根**，因为根目录里有 .env。
5. T4（业务 API/数据库）和 T5（MCP）只写设计文档，不实现。T5 的设计文档必须包含"MCP 补充阶段测试方案"（见 01 文档 §8 第 4 条），以官方 Everything 测试服务为测试对象。不要安装 Node.js、MCP 服务或 MCP SDK。Phase 3 在 T6 验收后结束，MCP 作为后续补充单独进行。
6. 不创建 Git 提交、不推送、不改 Tag，除非我明确授权。
7. Live 测试：T2 允许执行 1 次真实 Search 和 1 次真实 Fetch（`WEB_LIVE=1`，按成功请求计费，每次约 0.005 元）；T6 允许执行能力归属实测（`DEEPSEEK_LIVE=1`，对照组与实验组各至少 3 次）。密钥只从本机 .env 载入当前进程，不得打印、写入文件或提交。其余测试一律离线。

【第一步：只读，不改代码】
1. 按顺序完整阅读 skill 目录下：
   - SKILL.md
   - references/01_TOOL_FRAMEWORK.md
   - references/02_MERMAID_TOOLS.md
   - references/03_WEB_AND_FILE_SECURITY.md
   - references/04_DEEPSEEK_SEARCH_FETCH_CONTRACT.md
   - checklists/PHASE_ACCEPTANCE.md
2. 记录：git status --short；git branch --show-current；git log --oneline -6；git diff --stat。
3. 运行并记录真实结果，不采信 Skill 里的历史数字：
   .\.venv\Scripts\python.exe -m pytest -q
4. 阅读真实代码：ToolRegistry、ToolExecutor、ExecutionPolicy、RunClock、AgentLoop、Provider 适配层、Agent API 与 schema、EventEnvelope、Settings、现有测试。
5. 若你有联网能力，重新读取 04 文档开头列出的三个 DeepSeek 官方页面，与 04 对照；没有联网能力就在文档中标注"未对照最新官方文档"。
6. 把 Skill 的假设与真实代码逐项对照，写入 docs/phase3/BASELINE.md；类名、字段、文件位置与真实代码不一致时以真实代码为准，并记录到 docs/phase3/DEVIATIONS.md。不得假设任何已有类或方法存在。

【第二步：先汇报再动手】
输出：仓库现状、基线测试的真实统计、与 Skill 的差异、T0 的实施计划。无 P0/P1 阻塞则直接开始 T0。

【实施规则】
- 严格按 T0 → T1 → T2 → T3 → T6，不得跳阶段，不得一次性写完所有代码再验证。
- 每个里程碑结束：专项测试 + 全量 pytest；更新 docs/phase3/IMPLEMENTATION_LOG.md（命令、真实通过/失败/跳过数量、变更文件、风险）；再进入下一阶段。
- 默认行为必须与 M3 完全一致：不传新参数时 Agent 只有 calculator 与 get_current_time；API 变更只能是可选、向后兼容的新增字段或新增错误码。
- 复用现有 ToolRegistry/ToolExecutor/ExecutionPolicy/RunClock/EventEnvelope，不建第二套体系。
- 联网工具必须 async，不占用同步线程池（max_workers=2）。
- 默认测试全部离线；不得访问互联网。
- Mermaid：模型给结构化输入，服务端确定性生成并转义源码；validate_mermaid 只做安全与结构检查，guarantees_render 恒为 false；不实现服务端渲染。转义语法对照 Mermaid 官方文档核对并用测试固化。
- web_fetch 不做本地 HTML 解析、本地 DNS/重定向/IP 固定；做 URL 预校验、来源约束、结构化截断。
- 不新增依赖，除非现有依赖确实无法满足；先在 DEVIATIONS.md 论证。
- 不使用 eval/exec/shell=True；不记录 API Key、Authorization 头、完整参数或网页/文件正文。

【Git 与安全】
- 禁止 reset --hard、clean、强制 checkout/restore、改写历史；不移动任何 Tag。
- 工作区若有我未提交的修改，先识别并保护。
- 发现 P0/P1、安全风险、接口契约冲突或需要我决定的事项，立即停止并报告。

【诚信要求】
- 不虚报测试结果；推理不能当测试通过。
- 无法验证的写"未验证"；live 测试没跑写"未执行"。
- Windows 下因无法创建符号链接而跳过或替代的测试，如实标注。

【完成标准】
以 SKILL.md 第 6 节和 checklists/PHASE_ACCEPTANCE.md 为准，全部满足才可声称完成。最终报告包含：已实现工具与 toolset、新增/修改文件、API 请求与响应示例（含 toolsets、artifacts）、配置项、全量测试统计、live 测试执行情况、能力归属实测结果、已知限制、是否达到完成标准。
