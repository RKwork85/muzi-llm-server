# muzi-llm-server MCP 合规审计 Skill

只读审计包。用来判断当前 MCP 实现是否符合官方协议、是否具备可接受的架构、安全和上线条件。本包不改 Agent Runtime、Tool Executor、权限体系或依赖。

## 内容

```text
qs-llm-dp-mcp-compliance-audit-skill/
├── SKILL.md
└── README.md
```

`SKILL.md` 是本次审计的唯一主规范，内容与 `phase-skills/MCP Compliance Audit.md` 一致。原文件和 Phase 1、Phase 2、Phase 3 Skill 均未改动。

## 使用方式

1. 将本目录放在 `phase-skills/qs-llm-dp-mcp-compliance-audit-skill/`。
2. 完整阅读 `SKILL.md`。
3. 按 M0 到 M8 的顺序审计真实代码、依赖、配置和测试。
4. 报告写入 `docs/mcp-audit/`。
5. 没有代码、测试或实测证据的项目不得标为 PASS。

## 边界

- 第一轮只审计，不重写 MCP。
- 可以运行现有测试，也可以在临时目录做隔离验证。
- 外部 MCP Server、真实 OAuth 或公网条件做不到时，必须写明验证限制。
- 协议判断以官方文档为准，同时区分项目实际锁定的版本和最新规范。
