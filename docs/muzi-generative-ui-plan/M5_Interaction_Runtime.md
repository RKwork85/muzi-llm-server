# M5｜Interaction Runtime

## 阶段目标
支持用户提交、跳过、取消、超时与恢复，并保持权限、事务和幂等闭环。

## 前置条件
前序阶段验收通过；确认既有实现与本阶段契约兼容。

## 详细工作包
### M5-T01 状态机
- 工作内容：created→pending→submitted/skipped/cancelled/expired；处理 rejected、resumed；明确终态不可逆。
- 交付物：设计/代码变更、测试用例、审查记录。
- 完成定义：可演示、可测试、可回滚；异常路径明确。

### M5-T02 交互协议
- 工作内容：interaction_id、revision、action、payload、idempotency_key；响应含 accepted/status/event_cursor。
- 交付物：设计/代码变更、测试用例、审查记录。
- 完成定义：可演示、可测试、可回滚；异常路径明确。

### M5-T03 后端校验
- 工作内容：鉴权、owner/conversation/run 归属、action allowlist、payload schema、revision CAS、截止时间。
- 交付物：设计/代码变更、测试用例、审查记录。
- 完成定义：可演示、可测试、可回滚；异常路径明确。

### M5-T04 幂等与并发
- 工作内容：同键同请求重放返回原结果；同键不同内容 409；双击/多标签并发只有一个有效提交。
- 交付物：设计/代码变更、测试用例、审查记录。
- 完成定义：可演示、可测试、可回滚；异常路径明确。

### M5-T05 恢复编排
- 工作内容：提交结果关联暂停的 Run；区分恢复原 Run 和创建新 Run，避免无意重复工具执行。
- 交付物：设计/代码变更、测试用例、审查记录。
- 完成定义：可演示、可测试、可回滚；异常路径明确。

### M5-T06 持久化与审计
- 工作内容：interaction、submission、decision、outbox 原子提交；保留审计和失败原因。
- 交付物：设计/代码变更、测试用例、审查记录。
- 完成定义：可演示、可测试、可回滚；异常路径明确。

### M5-T07 前端体验
- 工作内容：pending/loading/success/conflict/expired；跳过与恢复、网络失败重试、可访问性反馈。
- 交付物：设计/代码变更、测试用例、审查记录。
- 完成定义：可演示、可测试、可回滚；异常路径明确。

## 关键验收标准
- [ ] 同一次提交重试不重复调用模型/工具
- [ ] 过期、跨用户、旧 revision、越权 action 被拒绝
- [ ] 用户跳过后运行策略符合模式约束
- [ ] 重启后 pending 交互可恢复
- [ ] 并发提交不会产生两个不同终态

## 建议文件位置（仅建议，审计后调整）
- `app/ui/interactions/service.py`
- `app/ui/interactions/models.py`
- `app/api/v1/endpoints/ui_interactions.py`
- `src/generative-ui/runtime/interactionClient.ts`
- `tests/integration/test_interaction_idempotency.py`

## 主要风险与防护
- 协议漂移：服务端 schema、客户端类型、测试样例统一生成或双向契约测试。
- 权限绕过：所有状态变更都在服务端复核，UI 组件不是授权凭证。
- 兼容性：加法迁移、feature flag、旧版 fallback、回滚路径。
- 竞态与重放：稳定 ID、事务、版本号、幂等键和事件序列。

## 统一工程约束
- 以现有 FastAPI / Vue 3 / PostgreSQL / Redis / SSE 为基线，先审计再修改；不得以规划推断当前实现状态。
- 后端是身份、授权、交互状态和事件序列的唯一可信来源；模型输出一律视为不可信输入。
- 协议必须版本化，旧客户端能安全降级；所有写操作具备鉴权、资源归属、幂等和审计。
- 每个阶段均需单元、集成、端到端、安全、兼容性测试；没有证据不得标记 ACCEPTED。
- 不擅自修改 Ask/Plan/Agent 现有语义，不以新 UI 协议替代 Runtime 权限边界。

## 阶段交付要求
- 提交架构决策记录（ADR）、协议/数据库迁移说明、测试报告、演示录屏或截图、回滚方案。
- 明确 CODE PASS、UAT PASS、PRODUCTION READY 三种不同状态。
- 每项验收需列出可复现命令、测试用例编号、预期结果、实际证据和已知限制。
