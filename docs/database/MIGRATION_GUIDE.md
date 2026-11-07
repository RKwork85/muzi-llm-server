# 迁移说明

Alembic 使用异步引擎，配置在 `alembic.ini` 和 `alembic/env.py`。`env.py` 通过 `Settings` 读取 `.env`，并导入 `app.models`，因此能看到全部九张表。

当前版本：

```text
6de1140597d2 (head)
```

说明是 `create core tables`。文件在 `alembic/versions/6de1140597d2_create_core_tables.py`。它创建九张表、索引、检查约束，以及 `updated_at` 触发器 `muzi_set_updated_at`。

在 `muzi-llm-server` 目录执行：

```powershell
python -m alembic upgrade head
python -m alembic current
python -m alembic check
```

`check` 用于确认数据库和模型没有结构差异。触发器不在 SQLAlchemy 模型里，`check` 不会把它们当成未生成的差异。

生成新迁移：

```powershell
python -m alembic revision --autogenerate -m "describe the change"
```

生成后要阅读文件，再执行 `upgrade`。自动生成不会替你写触发器变更。

回退：

```powershell
python -m alembic downgrade base
```

`downgrade` 会删除这些表和触发器。不要对还需要保留的 `muzi_llm` 执行。应用启动不会调用 Alembic，也不会在启动时做破坏性迁移。

验证时，回退和再次升级是在临时库 `muzi_llm_restore_check` 上做的。做法是给子进程设置 `POSTGRES_DB=muzi_llm_restore_check`。主库保持在 `head`。

离线 SQL 可以用：

```powershell
python -m alembic upgrade head --sql
```

这只打印 SQL，不连接执行。仍然需要 `.env` 里有数据库密码，因为环境模块会组装 URL。不要把输出贴到会保留密码的地方。URL 含有密码。
