# 备份和恢复

## 职责

PostgreSQL 备份保存用户、会话、消息和执行记录。脚本在容器里执行 `pg_dump`，格式是 custom（`-Fc`），文件写到 `deploy/docker/backups/`。文件名形如 `muzi_llm-yyyyMMdd-HHmmss.dump`。这些文件不提交 Git。

Redis 使用 RDB 和 AOF。AOF 为 `appendfsync everysec`，RDB 规则是 900 秒 1 次变更、300 秒 10 次变更、60 秒 10000 次变更。数据在卷 `muzi-redis-data`。这只保证缓存进程重启后还在，不能代替 PostgreSQL 的逻辑备份，也不包含业务表。

## 手动备份

```powershell
cd deploy\docker
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\backup.ps1
```

成功时日志 `backups/backup.log` 写 `OK`，并打印备份路径。失败时写 `ERROR`，删除不完整文件，并以非零退出码结束。备份成功后删除超过 30 天的 `muzi_llm-*.dump`。失败时不删除旧备份。

容器内通过本地套接字执行 `pg_dump`，命令行不放数据库密码。

## 计划任务

已登记 Windows 任务 `muzi-llm-postgres-backup`，状态为 Ready。触发器是每天 02:00，使用 Windows 本地时区。本机应为 `Asia/Shanghai`。任务调用 `deploy/docker/scripts/backup.ps1`。

重新登记：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\docker\scripts\register-backup-task.ps1
```

这里不使用 Linux cron。Docker 容器里也没有另外安装 cron。

## 恢复

恢复会用 `pg_restore --clean --if-exists` 改写目标库里的对象。必须同时给出备份文件、目标库和确认词：

```powershell
cd deploy\docker
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\restore.ps1 `
  -BackupFile .\backups\muzi_llm-20261107-170000.dump `
  -TargetDatabase muzi_llm `
  -Confirm YES
```

目标库必须已经存在。确认词不是 `YES` 时脚本拒绝执行。恢复 `muzi_llm` 会覆盖当前业务数据，执行前先另存一份新备份。

## 本次验证

集成测试完成了下面的闭环：

1. 向 `muzi_llm` 写入一条用户。
2. 运行 `backup.ps1`，确认 dump 存在且非空。
3. `pg_restore -l` 能列出备份内容。
4. 创建 `muzi_llm_restore_check` 并恢复。
5. 恢复库中的对应用户记录数为 1。
6. 只对恢复库执行 `downgrade base` 和 `upgrade head`。
7. 主库中的原记录仍然存在。
8. 删除临时库。
9. 人为放一个 40 天前的 dump，备份脚本把它删掉，30 天内的文件保留。

Redis 单独验证了密码认证、`SET`/`GET`、TTL、AOF 与 RDB 配置，以及容器重启后键仍在。PostgreSQL 也验证了容器重启后刚提交的行仍在。
