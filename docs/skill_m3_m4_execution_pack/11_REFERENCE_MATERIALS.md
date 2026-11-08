# 11 · 参考资料

以下资料用于理解行业通用模式，不代表要求照搬某个厂商的 SDK 或架构。项目现有实现和本包的安全边界优先。

## Agent Skills 格式与渐进式披露

1. Anthropic Agent Skills Overview  
   https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview  
   参考点：`SKILL.md` frontmatter、description 的触发用途、按需加载、资源引用与信任边界。

2. Anthropic Agent Skills Best Practices / Authoring  
   https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices  
   参考点：Skill 描述如何写、正文如何组织、渐进式披露、测试触发质量。若页面路径变化，以官方文档导航为准。

3. Anthropic：Equipping agents for the real world with Agent Skills  
   https://www.anthropic.com/research/equipping-agents-for-the-real-world-with-agent-skills  
   参考点：元数据预加载、正文按需读取、复杂资源分层。

4. Microsoft Agent Framework Skills  
   https://learn.microsoft.com/en-us/agent-framework/agents/skills  
   参考点：四阶段渐进式披露：advertise、load、read resources、run scripts。注意本项目 M3/M4 只实现前三类中的受控读取，不执行脚本。

## Agent Runtime / Orchestration

5. OpenAI Agents SDK  
   https://developers.openai.com/api/docs/guides/agents/sdk  
   参考点：应用自己拥有部署、工具实现、状态存储和审批决策时的 SDK 集成边界。

6. OpenAI Agents：Running agents  
   https://developers.openai.com/api/docs/guides/agents/running-agents  
   参考点：Agent loop、工具调用后的继续执行、暂停与恢复、运行状态。

7. OpenAI Agents：Agent definitions  
   https://developers.openai.com/api/docs/guides/agents/define-agents  
   参考点：Agent 的 instructions、tools、guardrails、handoffs 等职责边界。

8. OpenAI Agents：Integrations and observability  
   https://developers.openai.com/api/docs/guides/agents/integrations-observability  
   参考点：MCP 集成边界和 trace/可观测性。

## 参考原则

- 不复制厂商专有实现。
- 不为了“兼容标准”而破坏 M0–M2 已建立的协议。
- Agent Skill 格式标准不定义项目内部数据库、用户隔离和生命周期；这些由本项目负责。
- 参考文档中提到的脚本执行不属于本次范围。
- 外部文档可能更新；实施时记录访问日期与实际采用的设计点。
