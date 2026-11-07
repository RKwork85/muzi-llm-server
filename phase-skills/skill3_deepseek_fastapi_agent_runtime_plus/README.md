# DeepSeek FastAPI — Phase 3 Tool Ecosystem Skill

用于指导 Cursor 在已完成 M3 的 Agent Runtime 上扩展工具生态。目前只有 `calculator` 和 `get_current_time` 两个工具；本包增加工具框架升级、Mermaid 图表工具、联网搜索与网页抓取、沙箱只读文件工具。

## 里程碑

| 里程碑 | 内容 | 交付方式 |
|---|---|---|
| T0 | 工具框架升级（元数据、toolset、网络开关、调用上限） | 实现 |
| T1 | 本地工具 + Mermaid 工具 + artifacts | 实现 |
| T2 | web_search、web_fetch（DeepSeek 官方 Search/Fetch 接口） | 实现 |
| T3 | 沙箱只读文件工具 | 实现 |
| T4 | 业务 API / 数据库工具 | **仅设计评审**，等用户决定 |
| T5 | MCP 客户端 | **仅设计评审**，含"MCP 补充阶段测试方案"，Phase 3 之后单独补充 |
| T6 | 加固、文档、能力归属实测 | 实现 |

## 内容

```text
skill3_deepseek_fastapi_agent_runtime_plus/
├── SKILL.md
├── README.md
├── references/
│   ├── 01_TOOL_FRAMEWORK.md
│   ├── 02_MERMAID_TOOLS.md
│   ├── 03_WEB_AND_FILE_SECURITY.md
│   └── 04_DEEPSEEK_SEARCH_FETCH_CONTRACT.md
├── checklists/
│   └── PHASE_ACCEPTANCE.md
└── templates/
    ├── CURSOR_START_PROMPT.md
    └── CURSOR_CONTINUE_PROMPT.md
```

## 使用方式

1. 将本目录放到项目的 `phase-skills/skill3_deepseek_fastapi_agent_runtime_plus/`。
2. 新建 Cursor Agent 对话。
3. 把 `templates/CURSOR_START_PROMPT.md` 的内容发给 Cursor。
4. 每个里程碑完成后，对照 `checklists/PHASE_ACCEPTANCE.md` 审查，再让它继续。
5. 中断后用 `templates/CURSOR_CONTINUE_PROMPT.md` 续做。

## 设计要点

- **默认行为不变**：不指定新参数时，Agent 仍只有 calculator 和 get_current_time。
- **toolset 由服务端决定**：请求只能从服务端白名单里选名称，不能自带工具、schema 或 handler。
- **Mermaid 的分工**：模型提供结构化内容，本地工具确定性生成并转义 Mermaid；`validate_mermaid` 只是安全和结构的兜底检查，不保证可渲染。
- **外部内容都是不可信数据**：网页、搜索结果、文件内容不会被当作指令。
- **抓取由 DeepSeek 服务器执行**：本地不做 SSRF 级的网络防护，改为 URL 预校验 + 来源约束（防数据外泄）。
- **联网工具全部 async**，不占用 `max_workers=2` 的同步线程池。
- **所有新能力默认关闭**：需要服务端配置加请求选择 toolset 才生效。

## 重要说明

本包是工程规格和 Cursor 执行约束，不是已实现代码，也不保证 Cursor 自动正确。必须按里程碑审查代码、测试结果和安全边界。Search/Fetch 契约来自 2026-11-06 读取的 DeepSeek 官方文档，文档可能更新，Cursor 实施时要重新对照；Mermaid 的具体语法转义规则同样要对照官方文档核对并用测试固化，本包没有替它们背书。
