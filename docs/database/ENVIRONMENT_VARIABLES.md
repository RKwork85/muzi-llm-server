# 环境变量

有两份环境文件，用途不同。

| 文件 | 谁读取 | 是否提交 |
|---|---|---|
| `deploy/docker/.env` | Docker Compose | 否 |
| `muzi-llm-server/.env` | FastAPI `Settings` | 否 |
| `deploy/docker/.env.example` | 人工对照 | 是 |
| `muzi-llm-server/.env.example` | 人工对照 | 是 |

`scripts/init-env.ps1` 用随机数生成两份密码。文件已存在时不覆盖。示例文件里的密码是占位符 `replace-with-generated-password`。

## Compose

| 变量 | 示例 | 作用 |
|---|---|---|
| `POSTGRES_DB` | `muzi_llm` | 数据库名 |
| `POSTGRES_USER` | `muzi_admin` | 数据库用户 |
| `POSTGRES_PASSWORD` | 生成值 | 数据库密码 |
| `POSTGRES_HOST_PORT` | `5432` | 只绑定 `127.0.0.1` 的主机端口 |
| `REDIS_PASSWORD` | 生成值 | Redis 密码 |
| `REDIS_HOST_PORT` | `6379` | 只绑定 `127.0.0.1` 的主机端口 |
| `TZ` | `Asia/Shanghai` | 容器时区，也传给 PostgreSQL |

## FastAPI

| 变量 | 默认 | 作用 |
|---|---|---|
| `POSTGRES_HOST` | `127.0.0.1` | 数据库主机。进 Docker 后改为 `postgres` |
| `POSTGRES_PORT` | `5432` | 数据库端口 |
| `POSTGRES_DB` | `muzi_llm` | 数据库名 |
| `POSTGRES_USER` | `muzi_admin` | 数据库用户 |
| `POSTGRES_PASSWORD` | 空 | 数据库密码。`repr` 关闭，日志会打码 |
| `REDIS_HOST` | `127.0.0.1` | Redis 主机。进 Docker 后改为 `redis` |
| `REDIS_PORT` | `6379` | Redis 端口 |
| `REDIS_DB` | `0` | Redis 逻辑库，范围 0 到 15 |
| `REDIS_PASSWORD` | 空 | Redis 密码。`repr` 关闭，日志会打码 |
| `DATABASE_STARTUP_REQUIRED` | `false` | 为 `true` 时启动必须连上两个库，并要求密码至少 12 位 |
| `DB_POOL_SIZE` | `5` | 连接池大小 |
| `DB_MAX_OVERFLOW` | `10` | 连接池溢出 |
| `DB_POOL_TIMEOUT_SECONDS` | `30` | 等待连接池的时间 |
| `DB_POOL_RECYCLE_SECONDS` | `1800` | 连接回收时间 |
| `DB_CONNECT_TIMEOUT_SECONDS` | `10` | 建立连接超时 |
| `DB_COMMAND_TIMEOUT_SECONDS` | `30` | 单条命令超时 |
| `DB_STARTUP_ATTEMPTS` | `5` | 启动重试次数 |
| `DB_STARTUP_RETRY_SECONDS` | `0.5` | 首次重试间隔，之后加倍，上限 8 秒 |
| `REDIS_SOCKET_TIMEOUT_SECONDS` | `5` | Redis 套接字超时 |
| `REDIS_MAX_CONNECTIONS` | `20` | Redis 连接池上限 |

主机字段拒绝带账号或 URL 的值。密码不会出现在 `Settings` 的 `repr` 里。SQL 使用 SQLAlchemy 参数绑定。引擎关闭了 SQL echo，并打开 `hide_parameters`。

`DATABASE_STARTUP_REQUIRED=false` 时，现有测试和未配置数据库的进程不会创建连接。本机生成的 `.env` 使用 `true`。
