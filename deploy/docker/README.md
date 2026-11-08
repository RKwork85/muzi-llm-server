# muzi-llm 数据库

这组 PostgreSQL 和 Redis 只给 `muzi-llm-server` 使用。容器由 Docker Compose 管理，数据放在命名卷里。

## 镜像和容器

| 服务 | 镜像 | 容器名 | 主机端口 | 卷 | 网络 |
|---|---|---|---|---|---|
| PostgreSQL | `postgres:18` | `muzi-postgres` | `127.0.0.1:5432` | `muzi-postgres-data` → `/var/lib/postgresql` | `muzi-backend` |
| Redis | `redis:7` | `muzi-redis` | `127.0.0.1:6379` | `muzi-redis-data` → `/data` | `muzi-backend` |

PostgreSQL 18 的数据目录在卷内的版本子目录。不要把卷改挂到旧的 `/var/lib/postgresql/data`。

两个服务都是 `restart: unless-stopped`。PostgreSQL 用 `pg_isready` 做健康检查，Redis 用 `redis-cli ping`。健康检查通过不等于 FastAPI 可以省略自己的连接重试。

数据库名 `muzi_llm`，用户 `muzi_admin`。Redis 使用 0 号库，并要求密码。密码只放在本目录的 `.env`，由 `scripts/init-env.ps1` 生成。

## 启动和停止

在 `deploy/docker` 目录：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\init-env.ps1
docker compose up -d
docker compose ps
docker compose stop
docker compose down
```

`docker compose down` 不删除命名卷。需要连数据一起删除时才使用 `docker compose down -v`，这会清空数据库。

## 环境变量

见 `.env.example`。真实文件是 `.env`，已由服务端 `.gitignore` 的 `.env` 规则忽略。

| 变量 | 作用 |
|---|---|
| `POSTGRES_DB` | 数据库名，固定 `muzi_llm` |
| `POSTGRES_USER` | 数据库用户，固定 `muzi_admin` |
| `POSTGRES_PASSWORD` | 数据库密码 |
| `POSTGRES_HOST_PORT` | 主机端口，默认 `5432` |
| `REDIS_PASSWORD` | Redis 密码 |
| `REDIS_HOST_PORT` | 主机端口，默认 `6379` |
| `TZ` | 容器时区，`Asia/Shanghai` |

FastAPI 进程另有一套变量，说明在 `docs/database/ENVIRONMENT_VARIABLES.md`。Windows 本机运行时连接 `127.0.0.1`。以后 FastAPI 放进同一个 Docker 网络时，主机名改为 `postgres` 和 `redis`，端口仍是容器内的 `5432` 和 `6379`。

## DBeaver

- 主机：`127.0.0.1`
- 端口：`5432`
- 数据库：`muzi_llm`
- 用户：`muzi_admin`
- 密码：`deploy/docker/.env` 的 `POSTGRES_PASSWORD`
- SSL：禁用

表上的时间列是 `timestamptz`。PostgreSQL 内部按 UTC 保存，会话时区是 `Asia/Shanghai`，DBeaver 里看到的是上海时间。

## 迁移

在 `muzi-llm-server` 目录，使用已经写好数据库变量的 `.env`：

```powershell
python -m alembic upgrade head
python -m alembic current
python -m alembic downgrade base
```

应用启动不会自动执行迁移。`downgrade` 会删表，只在确认目标库之后手动执行。

## 备份和恢复

备份使用容器内的 `pg_dump` 自定义格式，文件放在 `backups/`，保留 30 天。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\backup.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\register-backup-task.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\restore.ps1 -BackupFile .\backups\<文件名>.dump -TargetDatabase muzi_llm -Confirm YES
```

恢复必须同时给出备份文件和 `-Confirm YES`。登记计划任务后，每天 02:00 按 Windows 本地时区执行备份。本机应设置为上海时区。

PostgreSQL 备份保存业务数据。Redis 的 RDB 和 AOF 只保证缓存卷在容器重启后还在，不能代替 `pg_dump`。

## 常见问题

- 端口被占用：确认 `5432` 和 `6379` 没有别的进程监听，或修改 `.env` 里的主机端口，并同步修改服务端 `.env`。
- 容器一直不健康：`docker logs muzi-postgres` 或 `docker logs muzi-redis`。不要把日志里的密码贴到外部。
- 密码认证失败：服务端 `.env` 和 `deploy/docker/.env` 的密码不一致。重新运行 `init-env.ps1` 只会在文件缺失时生成密码，不会覆盖已有密码。
- 卷是空的：确认挂载点是 `/var/lib/postgresql` 和 `/data`。
- FastAPI 启动报数据库不可用：先看 `docker compose ps` 是否为 healthy，再确认 `DATABASE_STARTUP_REQUIRED`。


## start
```
cd muzi-llm-server\deploy\docker
docker compose up -d
docker compose ps

```
