# 04 · M4 Runtime 详细规格

## M4.1 Runtime Domain

检查 M0–M2 是否已经有任务激活状态模型。复用现有模型，不创建重复状态机。

建议概念：
- `SkillActivation`：一次任务使用某个 Skill 的记录。
- `SkillSnapshot`：固定 skill_id、version、content_digest、owner_id、task_id、loaded_at。
- `RuntimeContext`：当前任务的已选 Skill 指令、资源引用、约束和元数据。
- `RuntimeResult`：状态、激活记录、输出元数据、错误信息。
- `RuntimeEvent`：activated / loaded / context_built / completed / failed / cancelled。

## M4.2 Activation

- 一个任务允许零个、一个或多个 Skill（上限必须配置）。
- 创建激活前检查 Discovery Decision 与用户授权。
- 激活记录必须绑定任务、用户和 Skill 版本。
- 激活状态与安装状态独立。
- Skill 被禁用或新版本发布，不得静默修改旧任务快照。
- 若任务尚未真正开始，是否允许取消/拒绝激活由明确策略决定。
- 幂等键/唯一约束应防止重试产生重复激活记录。

## M4.3 Context Builder

上下文建议分块：
1. 原有系统指令（最高优先级，不被 Skill 覆盖）。
2. 原有对话历史/Agent 任务上下文。
3. 用户当前任务。
4. 已选 Skill 的正文（明确标记为不可信的辅助指令）。
5. 受控引用资源内容（标记来源和资源名）。
6. 运行约束和可用工具说明（由系统策略提供，不由 Skill 自行授权）。

要求：
- 通过现有 Prompt Builder / Provider 请求构建链路接入。
- 不要在多个 API 层拼接字符串。
- 保留原有 Chat/Agent prompt 结构。
- 对 Skill 内容和引用资源设置 token/字符预算。
- 多 Skill 内容按明确顺序组装。
- 避免重复注入同一 Skill。
- 不允许 Skill 修改 system prompt、身份、工具权限或安全策略。
- 构建过程可测试，最好输出结构化 sections，而非只有不可检查的大字符串。

## M4.4 Resource Resolver

支持读取 Skill 包内被明确引用的资源文件（例如 references/*.md、templates/*），但必须：
- 只允许相对包根目录的规范化路径。
- 拒绝 `..`、绝对路径、盘符路径、UNC 路径、符号链接逃逸。
- 检查文件大小、类型、编码和允许扩展名。
- 按需读取，不递归预加载整个目录。
- 不执行资源。
- 不允许任意 URL 抓取；网络资源能力留给 M5/MCP/Tool 的独立授权体系。
- 记录读取资源的路径、摘要和大小（避免记录敏感正文）。

## M4.5 Lifecycle

建议生命周期（以现有状态机为准，不得重复造轮子）：

`CREATED → RESOLVING → LOADING → ACTIVE → COMPLETED`

异常终态：
`FAILED`、`CANCELLED`、`EXPIRED`（只有确实需要 TTL 时再实现）。

必须定义：
- 每个状态允许的迁移。
- 重复事件的幂等行为。
- 并发更新的乐观锁/事务策略。
- 任务取消与模型调用取消之间的关系。
- 崩溃恢复时如何识别未完成状态。
- 终态不可被普通更新重新打开。

## M4.6 Dispatcher

- 通过单一应用服务/适配器把 RuntimeContext 交给 Chat 或 Agent。
- Chat 和 Agent 可以有不同的上下文插槽，但 Discovery、加载、激活和快照规则必须共用。
- 不在 M4 重写 Agent Loop、工具执行、MCP 或模型 Provider。
- 运行时入口应支持 skill mode disabled，保证回退路径。
- 不要让 Skill 自己发起 Tool/MCP 调用；Skill 只是说明和流程指引。

## M4.7 Result Handler

记录：
- task_id / conversation_id（如适用）
- principal_id（服务端身份）
- skill_id / version / digest
- discovery decision / reason codes
- activation_id
- runtime status
- loaded resource identifiers
- duration / error code

不得记录完整密钥、认证头或不必要的 Skill 正文。

## M4.8 Recovery

处理：
- Skill 不存在/被卸载。
- 加载时状态变化。
- 版本变化。
- 文件损坏。
- 上下文超预算。
- 数据库/文件系统短暂错误。
- 任务取消。
- 模型调用失败。
- 重复请求/重试。
- 并发激活。

失败策略：
- 安全相关错误 fail closed。
- 可恢复基础设施错误可按有限次数重试。
- 上下文超限可减少资源、缩减候选或提示用户，不得静默截断关键指令。
- Skill Runtime 故障时，是否回退普通对话必须由策略明确决定并记录，不能把安全拒绝当成普通降级。

## M4.9 Tests

覆盖完整生命周期、合法/非法状态迁移、版本快照、重复激活幂等、并发、取消、恢复、上下文预算、资源路径安全、Chat/Agent 共用服务、禁用回退。

## M4 Definition of Done

- 任务可以激活多个明确版本的 Skill。
- 上下文可被结构化检查。
- Chat/Agent 共用同一套 Runtime 服务。
- 任务生命周期完整且状态迁移可验证。
- Skill 更新/禁用不会改变已激活快照。
- 资源读取受限且按需。
- 不执行第三方脚本。
- 失败、取消和恢复行为可测试、可审计。
