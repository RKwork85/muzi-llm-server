# 02 · 目标架构

## 1. 逻辑分层

```text
Chat API / Agent Runtime
        |
        v
Skill Integration Adapter
        |
        v
Skill Orchestrator (应用层编排，不承担业务规则)
        |
        +-------------------------------+
        |                               |
        v                               v
Discovery Service                 Runtime Service
        |                               |
        v                               v
Metadata Search                  Activation Manager
Matcher / Ranker                 Snapshot Resolver
Discovery Policy                 Context Builder
        |                         Resource Resolver
        v                         Lifecycle Manager
Existing Skill Registry                |
        |                               v
        +------------------------> Existing Skill Storage
```

安全策略是横切约束：Discovery 和 Runtime 在每个边界都必须执行现有用户隔离与状态校验。M5 再扩展可信等级、细粒度权限和沙箱。

## 2. 模块职责

### Discovery
- 从现有注册表/索引查询元数据。
- 应用当前用户隔离和可用状态过滤。
- 匹配、排序并给出理由。
- 不读取候选 Skill 的正文。
- 输出候选和建议动作，不直接执行 Skill。

### Loader
- 仅对已选择候选读取正文。
- 重新验证所有权、状态、版本、路径和摘要。
- 解析 frontmatter 与正文，拒绝非法或损坏内容。
- 不执行正文中的命令或脚本。

### Runtime
- 创建任务级激活记录。
- 固定 skill_id、version、content_digest、owner、task_id。
- 管理状态生命周期。
- 组装模型上下文。
- 提供受控资源读取。
- 不调用任意 shell/python 执行。

### Integration Adapter
- 将统一的 Skill Runtime 结果交给 Chat / Agent。
- 不在 Chat 和 Agent 内复制 Discovery、加载或快照逻辑。
- 不把 Skill 当成 Tool 注册或 MCP Server。

## 3. 推荐依赖方向

`apis / chat / agent → application services → domain contracts → registry/storage ports`

禁止：
- domain 直接依赖 FastAPI Request、数据库 Session 或 LLM SDK。
- Registry 反向依赖 Chat/Agent。
- Chat 和 Agent 各自实现独立 Skill loader。
- 在 API 层拼接 Skill 正文或直接读文件。

## 4. 关键时序

```text
1. Receive user message / agent task
2. Resolve authenticated principal from server context
3. Discovery(metadata only)
4. Apply policy and produce candidates
5. Select none / recommended / explicit skill(s)
6. For selected skills: load and revalidate
7. Create immutable task activation snapshot
8. Build bounded context package
9. Pass context package to existing Chat or Agent prompt builder
10. Run existing model/agent flow
11. Persist lifecycle result and structured events
12. Return existing response format plus optional skill trace metadata
```

## 5. 设计原则

- Progressive disclosure：先公开短元数据，选中后加载正文，引用资源再按需加载。
- Deterministic behavior：相同输入、同一索引和策略版本应得到可解释的排序结果。
- Explicit boundaries：discovery ≠ selection ≠ load ≠ activation ≠ execution。
- Immutable snapshot：任务激活固定版本与摘要。
- Fail closed：身份、所有权、版本或文件校验失败时拒绝加载。
- Backward compatibility：旧提示和原 Chat/Agent 默认行为保持不变。
