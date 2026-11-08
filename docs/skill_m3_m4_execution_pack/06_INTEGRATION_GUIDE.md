# 06 · Chat / Agent 集成指南

## 1. 先做只读追踪

Cursor 必须在改代码前，画出真实调用链：
- Chat 请求入口 → 身份上下文 → prompt 构建 → Provider 调用 → 响应。
- Agent 请求入口 → Agent Runtime → 规划/循环 → prompt 构建 → 工具执行 → 响应。
- 现有 `app/prompts/skills.py` 四条提示的引用位置。
- 当前 MCP、插件、代码执行入口及其文件范围（只读识别，不修改）。

输出真实文件路径、类/函数名和调用关系，不可凭目录名猜测。

## 2. 统一集成原则

推荐一个统一应用层入口，例如 `SkillOrchestrationService`（名称以项目风格为准）：
- 接收 principal、task、surface、mode。
- 调用 Discovery。
- 接收选择决策。
- 调用 Loader / Runtime。
- 返回可供 prompt builder 使用的 RuntimeContext。
- 不负责模型调用和 Tool 执行。

Chat Adapter 与 Agent Adapter 只负责把 RuntimeContext 映射到各自现有上下文插槽。

## 3. Chat

- 默认行为保持兼容。
- 若未启用 Skill 或无候选，完全沿用原 Chat 路径。
- 已选择 Skill 时，把结构化 Skill sections 传入现有 prompt builder。
- 不要直接在 endpoint 中拼接 Markdown。
- 流式响应协议不得因 Skill 元数据变化而破坏。
- 对外只暴露必要的 skill trace，例如 selected skill name/version；不要泄露内部路径和其他用户信息。

## 4. Agent

- Discovery/Runtime 与 Chat 共用。
- Agent 的 task_id、conversation_id、principal 必须沿用现有权威来源。
- Skill 不创建新的 Agent，不接管 Agent loop，不拥有 Tool 权限。
- 如果 Agent 多轮执行，必须定义 Skill 快照在整个任务中是否固定；默认固定，除非任务显式结束并开启新任务。
- Agent 重试不得重复激活或重复注入。

## 5. 四条旧提示

`research`、`polish`、`minutes`、`weekly`：
- 保留原定义和现有调用路径。
- 增加兼容测试。
- 如需桥接，桥接层应能回退。
- 不在本阶段强制转为注册表 Skill。
- 不允许因同名而覆盖用户安装的 Skill，必须定义确定性命名空间或优先级。

## 6. MCP / Plugin / Code Execution

- Skill 描述“如何做”，Tool/MCP 提供“能做什么”。
- M3/M4 不授予 Skill 调用任意工具的权限。
- 不改现有 MCP、插件和代码执行实现。
- 后续 M5/M6 再设计经过授权的能力桥接。
