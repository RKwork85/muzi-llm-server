# 与 Skill 3 的差异

## 事件名称

Skill 使用 `run_started`、`model_request_started` 这类描述。M1 已经把事件名固定为 `agent.started`、`agent.step.started`、`agent.tool.*`、`agent.completed`、`agent.failed`、`agent.cancelled`、`agent.limit_reached`。

实现保留这些名称，并追加 Skill 要求的模型阶段：

- `agent.model.started`
- `agent.model.completed`

没有另做一套平行事件名。

## 工具并发

Skill 允许在隔离和取消测试证明安全后再做受控并发。当前循环始终顺序执行。同步 handler 的线程池只是为了不阻塞事件循环，并限制同时占用的线程数。

## 402 余额错误

第一阶段公开错误码保持 `UPSTREAM_ERROR`，HTTP 状态保持 502，并且 402 不重试。`map_provider_exception` 额外记下 `upstream_status=402`。Agent 适配层看到这个内部状态后归类为 `balance` / `UPSTREAM_BALANCE`。直接抛出状态码 402 的异常同样归入余额不足。

## 参数上限的位置

工具参数的 8192 字符和 32 层嵌套是执行器常量，不是新的 `ExecutionPolicy` 字段，也没有因此放宽任何策略上限。

## 阶段间的全量测试

M3.0 单独跑过全量测试。M3.1 到 M3.6 的实现完成后，再跑了一次全量测试。中间阶段记录的是对应专项测试，不是每一段都各有一次全量数字。

## 其他

没有新增第三方依赖。没有启用 Beta `strict`。没有切换到 Responses API。思考模式继续使用 `Settings.deepseek_thinking`，默认 `disabled`。开启时如果工具调用缺少 `reasoning_content`，循环直接失败，不静默丢弃。
