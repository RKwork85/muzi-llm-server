# M1-M8 实施边界

M0 只建立基线。下面是后续阶段在当前仓库里的边界。

## M1 领域契约

新增 `app/skills/models.py` 和 `app/skills/protocols.py`。模型用 pydantic，风格对齐 `app/agents/models.py`。协议用 `typing.Protocol`。异常放在 `app/skills/errors.py`，需要对外的错误再映射到 `app/core/exceptions.py`。测试只验证契约和状态迁移，不接数据库。

## M2 注册

系统内置目录和本地目录扫描、ZIP 导入、SKILL.md frontmatter、查询、卸载、作用域和版本。文件放在工作区外的专用 Skill 根目录，路径规则复用文件沙箱的思想，不把正文存进数据库。同名冲突返回冲突记录，不覆盖。

## M3 发现和加载

可用集合按用户和作用域过滤。发现结果默认只有元数据。选中后再读 SKILL.md。references 和 assets 单独按相对路径加载，拒绝 `..` 和绝对路径。语义匹配通过可替换接口调用现有模型；测试使用假实现。

## M4 运行时

不新写 AgentLoop。激活结果是一段有预算的不可信说明，交给现有 `ContextManager` 或对话系统提示。Ask 只加载说明和授权引用。Agent 可以据此调用已经存在的工具，工具仍走 `ToolService` 或 `ToolExecutor`。失败时对话继续，不把 Skill 正文留在上下文里。

## M5 安全

第三方脚本默认不可执行。接口可以记录“请求执行某个脚本”，实际调用返回未启用。不把 `run_python` 说成隔离沙箱。路径穿越、跨用户读取和把 Skill 正文当成系统指令的测试要先于任何放行。

## M6 持久化

新 Alembic 版本增加 skills、skill_versions、skill_installations、skill_permissions、skill_execution_records、skill_resource_records。一次任务记录所固定的版本号。运行中的任务不跟随后来的升级。

## M7 API 和 SSE

路由进入 `app/api/v1/router.py`，前缀 `/api/v1/skills`，认证与现有用户接口一致。事件名使用实施说明里的 `skill_*`，但 payload 只含标识、状态和截断后的原因。

## M8

先跑现有 pytest，再跑 Skill 专项、安全、回归。真实模型联调用现有 DeepSeek Provider，并在报告里写明是否设置了 `DEEPSEEK_LIVE`。没有真实调用时，不能把 Mock 写成联调通过。
