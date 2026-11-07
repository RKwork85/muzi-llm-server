# 第一阶段验收清单

## A. 项目与配置
- [ ] 能在项目现有 Python 环境中安装依赖。
- [ ] `.env.example` 包含必要配置但不含真实密钥。
- [ ] 配置从环境变量加载，模型名可配置。
- [ ] 缺少 Key 时行为明确，不泄漏配置内容。
- [ ] SDK Async 客户端由应用 lifespan 管理并正确关闭。

## B. 对话
- [ ] 非流式接口能返回 DeepSeek 结果。
- [ ] messages 可携带多轮上下文。
- [ ] 请求参数校验有效。
- [ ] 模型覆盖值受 allowlist 控制。
- [ ] 不将客户端任意 system Prompt 当作可信服务端指令。

## C. SSE
- [ ] 返回 `text/event-stream`。
- [ ] 正确发送增量内容。
- [ ] 正确发送结束事件。
- [ ] 上游异常以安全错误事件表达。
- [ ] 客户端断开可取消上游请求。
- [ ] 流式与非流式共用核心业务逻辑，避免重复实现。

## D. Prompt
- [ ] 默认 Prompt 可用。
- [ ] Prompt 模板可按名称选择。
- [ ] 模板变量缺失时明确报错。
- [ ] Prompt 文件或配置不包含密钥。
- [ ] API 层不硬编码长 Prompt。

## E. Function Calling
- [ ] 至少提供 calculator 或 get_current_time 安全工具。
- [ ] Tool schema 符合 DeepSeek API 格式。
- [ ] 只执行服务端注册工具。
- [ ] 未注册工具拒绝执行。
- [ ] 非法 JSON 参数拒绝执行。
- [ ] 参数 schema 验证有效。
- [ ] 工具结果按 tool_call_id 回传。
- [ ] 工具循环有最大轮数和执行超时。
- [ ] 不使用 eval/exec 执行模型内容。
- [ ] 不默认暴露 Shell、任意文件访问或任意网络访问。

## F. 异常与日志
- [ ] 上游 401/403 映射为认证错误。
- [ ] 上游 429 映射为限流错误。
- [ ] 超时映射为 UPSTREAM_TIMEOUT。
- [ ] 5xx 映射为 UPSTREAM_ERROR。
- [ ] 记录 request_id、耗时、模型、usage（可用时）。
- [ ] 日志不包含 API Key、Authorization header。
- [ ] 客户端错误不包含堆栈和敏感上游原文。

## G. 自动化测试
- [ ] pytest 全部通过。
- [ ] 测试默认 mock 上游，不依赖真实 Key。
- [ ] 覆盖正常、异常、工具调用、SSE 路径。
- [ ] README 记录测试命令和结果。

## H. 文档与交付
- [ ] README 安装、配置、启动说明完整。
- [ ] Swagger `/docs` 可访问。
- [ ] 有 curl / PowerShell 请求示例。
- [ ] 有目录结构说明。
- [ ] 明确列出本阶段未实现能力。
- [ ] Cursor 输出真实测试结果，不虚报。
