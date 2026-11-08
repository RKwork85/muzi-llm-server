# 10 · 执行批次与检查清单

## 批次 0：审查（只读）
- [ ] 确认真实工作目录
- [ ] 读取本包所有文档
- [ ] 追踪 Chat 调用链
- [ ] 追踪 Agent 调用链
- [ ] 追踪认证 principal
- [ ] 盘点现有 Skill domain/registry/state machine
- [ ] 追踪旧四条提示
- [ ] 复核测试基线
- [ ] 输出差异报告和最小改动计划
- [ ] 等待用户批准

## 批次 1：M3 Discovery
- [ ] Discovery contracts
- [ ] Metadata-only search
- [ ] Intent matcher
- [ ] Candidate ranker
- [ ] Policy modes
- [ ] Lazy loader
- [ ] Loader revalidation
- [ ] Legacy adapter compatibility
- [ ] M3 unit/integration tests
- [ ] M3 documentation
- [ ] M3 acceptance report

## 批次 2：M4 Runtime
- [ ] Activation snapshot
- [ ] Lifecycle transitions
- [ ] Context builder
- [ ] Resource resolver
- [ ] Dispatcher
- [ ] Result handler
- [ ] Recovery and cancellation
- [ ] M4 unit/concurrency/failure tests
- [ ] M4 documentation
- [ ] M4 acceptance report

## 批次 3：Chat / Agent 联调
- [ ] Chat adapter uses shared service
- [ ] Agent adapter uses shared service
- [ ] Existing default behavior preserved
- [ ] Streaming response compatibility
- [ ] Retry/idempotency
- [ ] Error and fallback behavior
- [ ] End-to-end tests
- [ ] Integration report

## 批次 4：最终验收
- [ ] M3 acceptance matrix complete
- [ ] M4 acceptance matrix complete
- [ ] Security boundary tests complete
- [ ] All relevant tests run
- [ ] Full test suite run
- [ ] Historical failures compared
- [ ] Changed files listed
- [ ] Protected files untouched
- [ ] No unapproved database migrations
- [ ] No third-party script execution
- [ ] No Git checkpoint created without authorization
- [ ] CODE PASS / ACCEPTED / PRODUCTION READY stated separately

## 推荐实施节奏

一个批次完成后先检查 diff 和测试，再进入下一个批次。若出现必须修改保护范围内文件、数据库迁移、身份模型或 Agent 核心循环的情况，立即暂停并向用户说明，不自行扩大范围。
