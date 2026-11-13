# 部署说明

操作目录：`muzi-llm-server`。容器定义在 `deploy/docker/`。更短的操作步骤也写在 `deploy/docker/README.md`。

## 运行中的实例

2026-11-07 在本机 Docker Desktop 上启动并完成健康检查。

| 项 | PostgreSQL | Redis |
|---|---|---|
| 镜像 | `postgres:18` | `redis:7` |
| 实际版本 | PostgreSQL 18.6 | Redis 7.4.12 |
| 容器 | `muzi-postgres` | `muzi-redis` |
| 端口 | `127.0.0.1:5432` | `127.0.0.1:6379` |
| 卷 | `muzi-postgres-data` 挂在 `/var/lib/postgresql` | `muzi-redis-data` 挂在 `/data` |
| 网络 | `muzi-backend` | `muzi-backend` |
| 重启策略 | `unless-stopped` | `unless-stopped` |
| 健康检查 | `pg_isready` | `redis-cli ping` |
| 数据库时区 | `Asia/Shanghai` | 容器时区 `Asia/Shanghai` |

数据库名 `muzi_llm`，用户 `muzi_admin`。Redis 逻辑库是 `0`。密码只在本机 `deploy/docker/.env` 和 `muzi-llm-server/.env`，不写进仓库。

## 启动和停止

```powershell
cd deploy\docker
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\init-env.ps1
docker compose up -d
docker compose ps
docker compose stop
docker compose down
```

`down` 不删除卷。`down -v` 会清空数据，不要随便执行。

## 应用连接

Windows 上的 FastAPI 使用 `127.0.0.1`。连接串由 `app/core/database/urls.py` 根据配置生成，业务代码不写主机名。

以后 FastAPI 加入网络 `muzi-backend` 时：

```text
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
REDIS_HOST=redis
REDIS_PORT=6379
```

容器内端口不是主机映射端口。主机映射只给本机进程和 DBeaver 使用。

## DBeaver

1. 新建 PostgreSQL 连接。
2. 主机 `127.0.0.1`，端口 `5432`，数据库 `muzi_llm`，用户 `muzi_admin`。
3. 密码从 `deploy/docker/.env` 的 `POSTGRES_PASSWORD` 复制。
4. SSL 关闭。
5. 连接后执行 `SHOW timezone;`，结果应为 `Asia/Shanghai`。
6. `timestamptz` 在这个会话里按上海时间显示，存储仍是 UTC。

前端不能读取这些配置。配置模块在服务端，接口没有返回数据库连接信息。

## 迁移、备份和故障

迁移见 `MIGRATION_GUIDE.md`。备份见 `BACKUP_AND_RESTORE.md`。

常见问题：

- 健康检查一直是 `starting`：看 `docker logs muzi-postgres` 或 `docker logs muzi-redis`。不要外传含密码的日志。
- 应用启动报 PostgreSQL 或 Redis 不可用：先确认两个容器是 `healthy`，再确认服务端 `.env` 的密码和 `deploy/docker/.env` 一致，并且 `DATABASE_STARTUP_REQUIRED=true`。
- 端口冲突：修改两个 `.env` 里的端口，保持一致。
- 数据重启后消失：确认卷挂载点是 `/var/lib/postgresql` 和 `/data`，不要使用旧的 PostgreSQL 数据目录。
