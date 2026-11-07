# M3 阶段验收清单

每个阶段都必须填写实际结果，不允许预先勾选。

## 开始前

- [ ] 当前路径是唯一项目根目录
- [ ] 已读取 M0/M1/M2 文档和 REVIEW_REPORT.md
- [ ] 已检查 Git 状态，用户修改已保护
- [ ] 已读取 Provider、Settings、ToolRegistry、ToolExecutor 的真实实现
- [ ] 已核对当前 OpenAI SDK 版本和 DeepSeek API 文档

## M3.0 — P2 加固

- [ ] 成功工具结果始终是合法 JSON
- [ ] 结果截断不会伪装为完整成功
- [ ] 参数输入长度有上限
- [ ] JSON 嵌套深度有上限
- [ ] RecursionError 被安全处理
- [ ] 新增边界测试通过
- [ ] M1/M2 专项和全量回归通过

## M3.1 — Provider 适配

- [ ] Provider timeout 不超过总剩余时间
- [ ] SDK retries 不会突破总 Deadline
- [ ] assistant tool_calls 被正确解析
- [ ] tool_call_id/name/arguments 正确保留
- [ ] finish_reason 被明确映射
- [ ] thinking mode 若启用，reasoning_content 被正确回传
- [ ] 错误映射不泄露密钥
- [ ] Mock 测试覆盖，无需真实 API

## M3.2 — ContextManager

- [ ] system Prompt 和当前用户输入保留
- [ ] messages 数量和字符预算受限
- [ ] assistant tool_calls 与 tool results 不被拆散
- [ ] 每个 tool_call_id 有且仅有一个匹配结果
- [ ] 裁剪后仍是合法 Chat Completions 历史
- [ ] 超限和不完整历史有明确错误

## M3.3 — AgentLoop

- [ ] 纯文本回答完成
- [ ] 单工具调用闭环完成
- [ ] 多工具调用受控执行
- [ ] 未授权工具拒绝
- [ ] 重复 tool_call_id 防护
- [ ] 工具失败能安全回传
- [ ] max_steps/max_tool_calls 生效
- [ ] Deadline 生效
- [ ] 非正常 finish_reason 不会报告 completed

## M3.4 — Runtime / TaskManager

- [ ] 状态转换合法
- [ ] 终态只设置一次
- [ ] CancelledError 正确传播
- [ ] 任务创建/查询/取消/清理可用
- [ ] 活动任务数量有上限
- [ ] 同步工具线程并发有上限
- [ ] 线程实际结束前执行槽不释放
- [ ] 迟到结果不会污染状态或事件
- [ ] 不同运行相互隔离

## M3.5 — EventPublisher / SSE

- [ ] 事件队列有界
- [ ] 事件载荷过滤和脱敏
- [ ] 慢消费者不阻塞 Agent
- [ ] 队列满策略已测试
- [ ] SSE 断开不会默认取消任务
- [ ] 订阅资源能清理
- [ ] 终态可通过 TaskManager 查询

## M3.6 — API / E2E

- [ ] Agent API 输入白名单有效
- [ ] 客户端不能提交工具实现/schema/handler
- [ ] 创建、查询、取消 API 正常
- [ ] SSE 正常
- [ ] E2E 模型工具调用闭环通过 mock
- [ ] 超时/取消/错误/限额覆盖
- [ ] 第一阶段 API、Schema、错误码、SSE 兼容
- [ ] 全量测试通过
- [ ] 文档和实施记录齐全

## 最终

- [ ] 没有未解释的失败测试
- [ ] Skip 原因真实且透明
- [ ] `git diff --check` 通过
- [ ] 变更清单准确
- [ ] 已知限制列出
- [ ] 不夸大真实 DeepSeek live 验证范围
