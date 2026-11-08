# 依赖分析

## 允许的方向

`app/skills/` 可以依赖：

- `app/agents/context.py`、`policy.py`、`models.py` 的数据类型
- `app/tools/registry.py` 的只读查询
- `app/tools/file_sandbox.py` 的路径限制
- `app/core/database/`、`app/core/exceptions.py`、`app/core/logging.py`
- `app/models/base.py` 的 SQLAlchemy `Base`

Agent 和 Chat 可以在集成阶段依赖 `app/skills/` 的公开协议，用来发现和取得激活上下文。Tool Executor 不依赖 Skill。

## 禁止的方向

- Skill 不导入具体 Provider。
- Skill 不调用 `ToolExecutor._invoke` 或自己起子进程跑 Skill 包里的脚本。
- Skill 领域对象不继承 ORM 模型。ORM 只留在 persistence。
- 不把 `app/prompts/skills.py` 的字典当成注册表。

## 外部依赖

不新增第三方包。Markdown frontmatter 用现有 Python 标准库解析。ZIP 用 `zipfile`。语义匹配先用现有模型完成一次短分类调用，接口保持可替换，不引入向量库。

## 数据库

新迁移的 `down_revision` 必须是 `c4a8e2b71f03`。不修改已有表的列。执行记录用外键指向 `users.id` 和 `agent_tasks.id`，会话用可空的 `sessions.id`。组织或项目标识只作为可空文本保留，在没有对应主表之前不能写入启用记录。

## 事件

`EventPublisher` 会丢弃白名单以外的字段。Skill 事件如果直接复用它，必须先扩展白名单，并且每个字段仍受 `_MAX_VALUE` 长度限制。完整 SKILL.md 不能放进事件。
