# muzi-llm-server

DeepSeek 接入服务（FastAPI），为前端提供对话、Agent、数据处理、历史记录、鉴权、计费等能力。代码版本见 `app/__init__.py`。

它提供两条互不调用的路径：

- 对话：`ChatService` 使用受限工具注册表，负责普通对话与受控 Function Calling。
- Agent：`AgentRuntime` 使用完整注册表，模型只能调用本次请求允许的工具，服务端执行后把结果交回模型。

两条路径共用 DeepSeek Provider、提示词和服务配置。

## 目录结构

```text
app/
  main.py              # 入口、生命周期、异常和 request_id
  api/                 # 健康检查和 /api/v1
  core/                # 配置、日志、错误码、SSE、数据库
  agents/              # Agent 任务、循环、策略、产物
  services/            # 对话编排、提示词、工具执行
  providers/           # AsyncOpenAI 适配
  tools/               # 工具注册和各工具实现
  data/                # 表格处理、任务文件、上传客户端
  schemas/
alembic/               # 数据库迁移
deploy/docker/         # PostgreSQL / Redis 的 Docker Compose
```

配置从环境变量和本机 `.env` 读取。`.env.example` 只有占位符。

## 环境要求

- Python 3.11 及以上
- Docker + Docker Compose（用于 PostgreSQL 和 Redis）
- pip

## 快速开始（开发环境）

```bash
# 1. 进入后端目录
cd muzi-llm-server

# 2. 创建并激活虚拟环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1     # Windows PowerShell
# source .venv/bin/activate      # macOS / Linux

# 3. 安装依赖
python -m pip install -r requirements.txt

# 4. 准备环境变量
Copy-Item .env.example .env      # Windows PowerShell
# cp .env.example .env           # macOS / Linux

# 5. 启动数据库（PostgreSQL + Redis）
cd deploy/docker
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\init-env.ps1
docker compose up -d
docker compose ps
cd ../..

# 6. 执行数据库迁移
python -m alembic upgrade head

# 7. 启动服务
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

启动后：

- Swagger：`http://127.0.0.1:8001/docs`
- 存活检查：`http://127.0.0.1:8001/health`
- 就绪检查：`http://127.0.0.1:8001/ready`

## 安装依赖

```bash
python -m pip install -r requirements.txt
```

生产环境建议锁定版本，例如：

```bash
python -m pip freeze > requirements.lock.txt
python -m pip install -r requirements.lock.txt
```

## 项目配置

### 后端环境变量

复制 `.env.example` 为 `.env` 后填写。密钥只放在本机 `.env`，不提交到仓库。常用变量：

| 变量 | 作用 | 默认 |
|---|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek 密钥。为空时进程可启动，调用模型返回 `CONFIGURATION_ERROR` | 空 |
| `DEEPSEEK_BASE_URL` | OpenAI SDK 的 base URL，不要带 `/chat/completions` | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 默认模型，必须在白名单内 | `deepseek-flash` |
| `DEEPSEEK_MODEL_ALLOWLIST` | 允许的模型，逗号分隔 | `deepseek-flash,deepseek-v4-pro` |
| `LLM_PROVIDER` | 模型提供方 | `deepseek` |
| `LOG_LEVEL` | 日志级别 | `INFO` |
| `RUNTIME_ENV` | 运行环境，生产填 `production` | `development` |

其余字段与取值范围见 `app/core/config.py` 与 `.env.example`，改 `.env` 后需重启进程。

### 数据库连接

数据库地址来自环境变量，业务代码不写死主机名：

| 变量 | 作用 |
|---|---|
| `POSTGRES_HOST` | PostgreSQL 主机，本机运行填 `127.0.0.1` |
| `POSTGRES_PORT` | PostgreSQL 端口，默认 `5432` |
| `POSTGRES_DB` | 数据库名，固定 `muzi_llm` |
| `POSTGRES_USER` | 数据库用户，固定 `muzi_admin` |
| `POSTGRES_PASSWORD` | 数据库密码，与 `deploy/docker/.env` 保持一致 |
| `REDIS_HOST` | Redis 主机，本机运行填 `127.0.0.1` |
| `REDIS_PORT` | Redis 端口，默认 `6379` |
| `REDIS_PASSWORD` | Redis 密码，与 `deploy/docker/.env` 保持一致 |
| `DATABASE_STARTUP_REQUIRED` | 启动时强制数据库可用，生产填 `true` |

### 对象存储（可选）

文件上传工具依赖 qs-oss 服务：

| 变量 | 作用 |
|---|---|
| `OSS_UPLOAD_BASE_URL` | 上传服务地址，指向 qs-oss。留空则上传工具返回 `OSS_UNAVAILABLE` |
| `OSS_UPLOAD_PREFIX` | 上传路径前缀，默认 `agent-data` |

本服务不保存对象存储 AccessKey，凭证只放在 qs-oss 服务端。

## 数据库

数据库由 `deploy/docker` 下的 Docker Compose 管理，PostgreSQL 18 + Redis 7，数据保存在命名卷。

### 启动与停止

```bash
cd deploy/docker
docker compose up -d
docker compose ps
docker compose stop
docker compose down
```

`docker compose down` 不删除数据；只有确认清空数据时才用 `docker compose down -v`。

### 密码初始化

`init-env.ps1` 会在 `.env` 缺失时生成数据库密码：

```bash
cd deploy/docker
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\init-env.ps1
```

生成后把 `deploy/docker/.env` 里的 `POSTGRES_PASSWORD`、`REDIS_PASSWORD` 同步填写到后端 `.env`。

### 迁移

```bash
# 在 muzi-llm-server 目录执行
python -m alembic upgrade head
python -m alembic current
```

应用启动不会自动执行迁移。`downgrade` 会删表，只在确认目标库后手动执行：

```bash
python -m alembic downgrade base
```

## 启动服务

开发（带热重载）：

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

生产（不开启 reload）：

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001
```

端口需与前端反向代理或 `VITE_API_BASE_URL` 保持一致（默认约定为 `8001`）。

## 生产部署

生产环境至少满足以下条件：

1. 在 `.env` 中设置 `RUNTIME_ENV=production` 和 `DATABASE_STARTUP_REQUIRED=true`，并填写数据库密码。
2. 启动 PostgreSQL / Redis 并完成迁移。
3. 填写 `DEEPSEEK_API_KEY`。
4. 需要文件上传时，启动 qs-oss 服务并配置 `OSS_UPLOAD_BASE_URL`。

注意：生产模式下若 `DATABASE_STARTUP_REQUIRED=false` 或数据库不可用，进程会拒绝启动，防止回退到内存授权。

## 必要说明

- 缺少 Key 时，响应只有「模型服务配置不完整」，不会回显变量值；日志会抹掉 API Key 和 `Authorization: Bearer`。
- 数据库不会在应用启动时自动建表，必须先执行 `alembic upgrade head`。
- Agent 任务保存在进程内存中，生产环境默认使用单进程（单 worker）；需要多 worker 横向扩展时需引入共享存储。
- 数据与工作区目录不能指向项目根：`DATA_STORE_ROOT` 与 `TOOLS_WORKSPACE_ROOT` 的配置受此限制。
- 不执行模型给出的任意代码，不提供任意路径的写入、删除或移动。
- `.env` 已被 git 忽略，不要提交真实密钥。

## 错误码

错误响应统一包含 `error.code`、`error.message`、`error.request_id`。常见错误码：`CONFIGURATION_ERROR`、`UPSTREAM_AUTH_ERROR`、`UPSTREAM_RATE_LIMIT`、`UPSTREAM_TIMEOUT`、`UPSTREAM_ERROR`、`INVALID_MODEL_RESPONSE`、`INVALID_TOOL_ARGUMENTS`、`TOOL_NOT_ALLOWED`、`TOOL_EXECUTION_ERROR`、`TOOL_ROUND_LIMIT`、`REQUEST_VALIDATION_ERROR`、`MODEL_NOT_ALLOWED`、`PROMPT_NOT_FOUND`、`AGENT_TASK_NOT_FOUND`、`AGENT_TASK_LIMIT`、`INTERNAL_ERROR` 等。
