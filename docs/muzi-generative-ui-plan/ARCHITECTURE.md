# 目标架构与统一契约

```text
User → Vue Dynamic Renderer ↔ Interaction API
                   ↑                 ↓
             SSE Event Stream ← Outbox/Event Store
                   ↑                 ↑
Ask / Plan / Agent Runtime → UI Orchestrator
                               ├ UI Policy Engine
                               ├ Component Registry
                               ├ Schema Validator
                               └ Model UI Planner
                   PostgreSQL / Redis / Object Storage
```

## 领域对象
- Conversation：身份与会话归属。
- Run：一次逻辑执行；Attempt：一次执行尝试。
- Message：有序 Block 容器；Block：稳定 ID、类型、版本、内容、状态。
- Interaction：待提交用户决策，绑定 Run/Message/Block。
- Event：可重放的状态变更，带序列号与协议版本。
- UI Patch：限定操作、基准 revision、目标 revision。

## 信任边界
1. 模型仅输出 UI 意图；不授予权限。
2. 前端仅渲染注册表组件；不执行模型提供的脚本。
3. 后端验证 JWT、资源归属、操作 allowlist、revision、payload schema、幂等键。
4. 状态写入与 outbox 处于同一事务；SSE 可重复投递，客户端幂等消费。
5. Ask/Plan/Agent 共用 UI 基建，但各自 Runtime 策略不被覆盖。

## API 建议（非现有事实）
- `GET /api/v1/ui/capabilities`：按模式/客户端版本返回可用组件。
- `GET /api/v1/conversations/{id}/messages`：返回混合消息。
- `GET /api/v1/runs/{id}/ui/snapshot`：状态快照及 cursor。
- `GET /api/v1/runs/{id}/events?cursor=...`：增量事件/SSE。
- `POST /api/v1/ui/interactions/{id}/actions`：提交交互。
- `POST /api/v1/ui/patches`：仅服务端内部或受授权调用，不开放模型直写。

## 数据表建议（审计后定）
`ui_message_blocks`、`ui_interactions`、`ui_interaction_submissions`、`ui_events`、`ui_outbox`、`ui_snapshots`。若现有 Plan 表可复用，应采用映射/扩展而非重复创建权威状态。

## 非功能目标（建议验收阈值，须基线实测后确认）
- UI 事件序列严格可恢复；重放不产生额外写入。
- 组件注册表支持版本与 fallback。
- 对外可观测：run_id、message_id、block_id、interaction_id、event_id、trace_id。
- 安全：跨用户读取/写入、重放、竞态、XSS、越权调用均覆盖。
