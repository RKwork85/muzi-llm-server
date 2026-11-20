# 后端消息队列接口使用文档

本文档说明本项目后端用到的三套「队列 / 异步消息」能力，以及它们的调用方式、配置、状态机和排障方法：

| 能力 | 实现 | 存储位置 | 消费者 | 典型用途 |
| --- | --- | --- | --- | --- |
| 内容创作任务队列 | Taskiq `ListQueueBroker` | Redis DB `TASKIQ_REDIS_DB`（默认 `2`） | `python -m app.content.worker` | 采集、AI 工作流、平台发布 |
| ComfyUI 生成队列 | 自研 Redis 有序集合（ZSET） | Redis DB `COMFY_REDIS_DB`（默认 `1`） | `muzi-comfy-service` 进程内 `worker_loop` | 图像 / 视频生成排队与 GPU 串行调度 |
| 短信回执队列 | 阿里云 MNS | 云端队列 | 主服务后台协程 | 拉取短信发送回执 |

> 三套队列都复用同一个 Redis / 云资源，但用**独立 DB 或独立键前缀**隔离，互不干扰。
> 任务真相（状态、进度、结果）最终都落在 PostgreSQL 或业务日志里，Redis 只负责传递与排队。

---

## 1. 内容创作任务队列（Taskiq + Redis）

### 1.1 模块结构

| 文件 | 职责 |
| --- | --- |
| `app/content/broker.py` | 创建 `ListQueueBroker` 实例，构造 Redis URL |
| `app/content/tasks.py` | 用 `@broker.task` 注册任务；`_execute` 负责短事务执行、幂等检查、失败落库 |
| `app/content/worker.py` | worker 进程入口，消费 broker 队列 |
| `app/content/service.py` | 任务状态机、收单 / 提交 / 查询 / 取消、SSE 事件发布 |
| `app/content/states.py` | `TaskStatus` / `TaskStage` 与合法流转表 |
| `app/content/events.py` | 进程内队列 + Redis pub/sub 的 SSE 事件广播 |
| `app/api/v1/endpoints/content.py` | 对前端暴露的 HTTP 接口与 SSE 接口 |

### 1.2 配置

主服务 `.env` 中与队列相关的配置：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `REDIS_HOST` | `127.0.0.1` | Redis 地址 |
| `REDIS_PORT` | `6379` | Redis 端口 |
| `REDIS_PASSWORD` | 空 | Redis 密码，非空时会做 URL 编码后拼进 broker URL |
| `REDIS_DB` | `0` | 主服务业务键所在 DB |
| `TASKIQ_REDIS_DB` | `2` | Taskiq 队列专用 DB，避免与业务键混用 |

对应代码：`app/core/config.py`、`app/content/broker.py`。

broker URL 形如：

```text
redis://:PASSWORD@127.0.0.1:6379/2
```

两个容易踩坑的点：

1. **密码必须 URL 编码**：`broker.py` 使用 `urllib.parse.quote(..., safe="")`，否则密码里的 `@`、`:`、`/` 会破坏 URL 解析。
2. **必须关闭 socket 超时**：`ListQueueBroker(url=..., socket_timeout=None)`。redis-py 8.x 默认 `socket_timeout=5s`，会让 worker 的阻塞 `BRPOP` 每 5 秒超时一次并反复重连。

> 实际队列键是 Redis List，默认键名为 `taskiq`（见 `taskiq_redis.ListQueueBroker` 的 `queue_name` 默认值）。
> 因此排查队列深度时使用 `LLEN taskiq`，并确认连的是 `TASKIQ_REDIS_DB` 指定的 DB。

### 1.3 已注册的任务

定义在 `app/content/tasks.py`：

| 任务名（`task_name`） | 函数 | 入参 | 预期阶段 / 状态 |
| --- | --- | --- | --- |
| `content.collect` | `collect_task` | `task_id: str` | `collect` / `queued` |
| `content.workflow` | `workflow_task` | `task_id: str` | `workflow` / `queued` |
| `content.publish` | `publish_task` | `task_id: str` | `publish` / `publishing` |

任务只传 `task_id`，业务所需数据（来源链接、配置、交付类型）都在 `content_tasks` 表里，避免消息体膨胀与数据不一致。

### 1.4 生产者：投递任务（`.kiq()`）

在 API handler 中投递，**必须遵循「先 `commit`，再 `kiq`」**：

```python
from app.content.tasks import workflow_task

task = await ContentService().submit(
    session, user.id, body.task_id, body.deliver_type, body.config
)
await session.commit()                 # 1. 先让任务行真正落库
await workflow_task.kiq(str(task.id))  # 2. 再入队
```

原因：FastAPI 的 `BackgroundTasks` 实际在依赖 teardown（`get_db_session` 的 commit）之前执行。
若用它投递，worker 可能先于 commit 读到旧的 `draft` 状态，幂等检查会直接跳过任务。
显式提交后再投递，才能保证「任务行落库」与「入队」严格有序。

`broker` 是惰性连接的，主服务 import `app.content.tasks` 只为拿到任务对象，不会在 import 时建立 Redis 连接。

### 1.5 消费者：启动 worker

```powershell
cd muzi-llm-server
.\.venv\Scripts\python.exe -m app.content.worker
```

等价于：

```powershell
.\.venv\Scripts\taskiq.exe worker app.content.tasks:broker --workers 1
```

worker 的行为约定（`app/content/tasks.py::_execute`）：

1. `_ensure_backends()` 在每个 worker 进程内初始化一次 SQLAlchemy 引擎与 Redis。
2. 打开一个**短事务**，按 `task_id` 读取 `content_tasks`。
3. 幂等检查：`task.stage == expected_stage and task.status == expected_status`，不满足就记日志并跳过（任务已取消 / 重复投递 / 状态已推进）。
4. `dispatch()` 按 `stage` 路由到 `run_collect` / `run_workflow` / `run_publish`。
5. 成功则 `commit`；任何异常则 `rollback`，再由 `_mark_failed` 把任务落到 `failed` / `publish_failed`，写入 `error`（截断 2000 字符），最后重新抛出异常供 Taskiq 记录。

worker 启动日志出现类似内容即表示正常：

```text
INFO taskiq ... Starting 1 worker(s)...
INFO app.content.tasks ... content task id=... skip: expect=workflow:queued actual=...
```

### 1.6 任务状态机

`app/content/states.py` 是唯一事实来源。合法流转：

```text
collect:   queued     -> running | canceled | failed
           running    -> draft | failed | canceled
           draft      -> queued（提交任务节点，切到 workflow）| failed | canceled
           failed     -> queued（重试）

workflow:  queued     -> running | canceled | failed
           running    -> succeeded | failed | canceled
           failed     -> queued（重试）
           succeeded  -> publishing（发起发布）

publish:   publishing -> published | publish_failed
           publish_failed -> publishing（发布重试）
```

终态：`published`、`canceled`（`TERMINAL_STATUSES`）。

注意：

- `draft` 只出现在 `collect` 阶段；`publishing` / `publish_failed` 只出现在 `publish` 阶段。
- `queued` / `running` 在两个阶段含义不同，必须结合 `stage` 判断。
- 取消只对 `queued` / `running` 开放。`queued` 时 worker 的幂等检查会直接跳过，零成本；`running` 时当前实现不会中断已经开始的执行，属于尽力而为。

### 1.7 HTTP 接口

统一前缀：`/api/v1/content`
统一鉴权：`Authorization: Bearer <access_token>`（`get_authenticated_user`），`user_id` 从登录态注入，客户端不能伪造。

统一错误结构：

```json
{
  "error": {
    "code": "CONTENT_TASK_STATE_INVALID",
    "message": "任务当前状态不支持该操作",
    "request_id": "xxx"
  }
}
```

常见错误码：

| HTTP | code | 场景 |
| --- | --- | --- |
| `401` | `LOGIN_REQUIRED` | 未带 token / token 失效 |
| `404` | `CONTENT_TASK_NOT_FOUND` | 任务不存在或不属于当前用户 |
| `409` | `CONTENT_TASK_STATE_INVALID` | 当前状态不允许该操作（如取消已完成任务） |
| `422` | `REQUEST_VALIDATION_ERROR` | 参数校验失败 |
| `503` | `DATABASE_UNAVAILABLE` | 数据库未就绪 |

#### 1.7.1 采集收单

```http
POST /api/v1/content/collect
Content-Type: application/json
Authorization: Bearer <token>
```

请求：

```json
{
  "links": [
    { "platform": "xiaohongshu", "url": "https://www.xiaohongshu.com/explore/xxxx" },
    { "platform": "douyin", "url": "https://www.douyin.com/video/xxxx" }
  ]
}
```

约束：`links` 长度 1~50；`platform` 1~64 字符；`url` 1~2048 字符；`extra="forbid"`（多余字段报 422）。

响应：

```json
{
  "task_id": "b3f1c2d4-...",
  "status": "queued",
  "stage": "collect"
}
```

行为：新建 `content_tasks` 行（`queued/collect`）→ `commit` → 投递 `content.collect`。

#### 1.7.2 提交任务节点

```http
POST /api/v1/content/submit
```

请求：

```json
{
  "task_id": "b3f1c2d4-...",
  "deliver_type": "image-text",
  "config": {
    "style": "干货分享",
    "length": 800,
    "tone": "口语化",
    "keywords": ["AI", "副业"],
    "count": 1
  }
}
```

`deliver_type` 取值：`image-text` | `note` | `video`。

响应：完整 `TaskOut`（见 1.7.6）。

行为：`draft/collect -> queued/workflow`，写入 `deliver_type` / `config`，`commit` 后投递 `content.workflow`。

#### 1.7.3 我的任务列表

```http
GET /api/v1/content/tasks?status=queued&limit=50&offset=0
```

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `status` | 否 | 按状态过滤 |
| `limit` | 否 | 默认 50，范围 1~200 |
| `offset` | 否 | 默认 0 |

响应：

```json
{
  "items": [ { "...": "TaskOut" } ],
  "total": 12,
  "limit": 50,
  "offset": 0
}
```

#### 1.7.4 查询任务详情

```http
GET /api/v1/content/tasks/{task_id}
```

响应：`TaskOut`。

#### 1.7.5 取消任务

```http
POST /api/v1/content/tasks/{task_id}/cancel
```

无请求体。仅 `queued` / `running` 可取消，其余状态返回 `409 CONTENT_TASK_STATE_INVALID`。

响应：`TaskOut`（`status=canceled`）。

#### 1.7.6 发起发布

```http
POST /api/v1/content/tasks/{task_id}/publish
```

无请求体。要求任务处于 `succeeded/workflow`，流转到 `publishing/publish`，`commit` 后投递 `content.publish`。

响应：`TaskOut`。

#### 1.7.7 `TaskOut` 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID | 任务 ID |
| `status` | string | 见状态机 |
| `stage` | string | `collect` / `workflow` / `publish` |
| `deliver_type` | string \| null | 交付链路 |
| `source_links` | array | 采集来源 `[{platform, url}]` |
| `config` | object | 工作流配置 |
| `progress` | object | `{percent, current_step, total_steps, step_key, step_name}` |
| `preview_data` | object \| null | 采集预览数据 |
| `result` | object \| null | 最终交付物结果 |
| `error` | string \| null | 失败原因 |
| `retry_count` | int | 重试次数 |
| `queued_at` / `started_at` / `finished_at` | datetime \| null | 生命周期时间 |
| `created_at` / `updated_at` | datetime | 创建 / 更新时间 |

#### 1.7.8 curl 完整示例

```bash
BASE=http://127.0.0.1:8000/api/v1
TOKEN=<access_token>

# 1) 采集收单
curl -X POST "$BASE/content/collect" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"links":[{"platform":"xiaohongshu","url":"https://example.com/note/1"}]}'

# 2) 查询任务
curl "$BASE/content/tasks/<task_id>" -H "Authorization: Bearer $TOKEN"

# 3) 提交任务节点
curl -X POST "$BASE/content/submit" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"task_id":"<task_id>","deliver_type":"image-text","config":{"style":"干货"}}'

# 4) 订阅进度（SSE）
curl -N "$BASE/content/tasks/<task_id>/events" -H "Authorization: Bearer $TOKEN"
```

### 1.8 SSE 进度订阅

```http
GET /api/v1/content/tasks/{task_id}/events
Authorization: Bearer <token>
Accept: text/event-stream
```

- 鉴权使用 `get_stream_user_id`：单独开短生命周期 session 解析 `user_id` 后立即关闭，**不占用请求级数据库连接**（否则连接池会被长连接抽干）。
- 事件名即事件类型，`data` 为 JSON。
- 空闲每 15 秒发送注释帧 `: ping` 保活。
- 同一用户可能同时有多个任务，服务端会按 `task_id` 过滤后推送。
- 连接建立后先收到 `: connected`。

事件类型：

```text
event: content.task.state
data: {"type":"content.task.state","task_id":"...","status":"running","stage":"workflow","error":null,"result":null}

event: content.task.progress
data: {"type":"content.task.progress","task_id":"...","status":"running","stage":"workflow","progress":{"percent":29,"current_step":2,"total_steps":7,"step_key":"clean","step_name":"内容清洗"}}
```

实现要点（`app/content/events.py`）：

- **进程内**：`asyncio.Queue` 扇出，Redis 不可用时也能工作，覆盖单进程部署。
- **跨进程**：Redis pub/sub，频道 `muzi:content:events:{user_id}`。worker 是独立进程，写库后靠这条通道把事件送回主服务，再推给浏览器。
- 事件信封带 `process` 字段；发布端已就地扇出，桥接收到自己发出的回声时按该字段丢弃，避免重复推送。
- 事件在 `session.flush()` 之后、`commit()` 之前发布，存在毫秒级窗口，订阅方立刻读库可能读到旧值。内容进度不是资金操作，前端另有轮询兜底，可接受。

### 1.9 新增一种队列任务的检查清单

1. 在 `app/content/states.py` 增加状态 / 阶段与合法流转（如需要）。
2. 在 `app/models/constants.py` 同步持久化校验用的字符串元组（状态机是唯一事实来源，模型约束据此生成）。
3. 在 `app/content/tasks.py` 写业务函数，并在 `@broker.task(task_name="content.xxx")` 中注册。
4. 在 `dispatch()` 增加 `stage` 分支。
5. 在 `app/api/v1/endpoints/content.py` 的写接口里按「`commit` → `.kiq()`」投递。
6. 重启 worker 进程（任务注册在 import 时完成，新增任务必须重启才生效）。

### 1.10 运维与排障

| 现象 | 排查方向 |
| --- | --- |
| 接口返回 `queued`，但状态一直不动 | worker 未启动；或连错 Redis DB / 密码错误；`LLEN taskiq` 是否有积压 |
| worker 日志大量 `skip` | 重复投递或状态已推进，属正常幂等；检查是否有重复 `.kiq()` |
| 任务落到 `failed` | 看 `content_tasks.error` 与 worker 异常堆栈；修复后把任务重新置为 `queued` 并重新投递 |
| SSE 无事件 | worker 与主服务是否连同一个 Redis；频道前缀是否为 `muzi:content:events:`；浏览器是否带 token |
| 服务关闭卡住 | `close_all()` 必须被调用（`lifespan` 已处理）；心跳与关闭标记共同保证生成器退出 |
| 队列消息丢失 | `ListQueueBroker` 使用 `BRPOP`，消息取出即出队，无 ack / 可见性超时；进程在取出后崩溃会丢这一条。任务行仍在库中，可重新投递恢复 |

常用命令：

```powershell
# 查看队列深度（注意 -n 2 对应 TASKIQ_REDIS_DB）
redis-cli -n 2 LLEN taskiq

# 查看队列头部（内容是 Taskiq 序列化后的消息，不可读属正常）
redis-cli -n 2 LRANGE taskiq 0 4

# 查看业务库中的任务状态
# SELECT id, stage, status, error FROM content_tasks ORDER BY created_at DESC LIMIT 20;
```

> 清理队列前务必确认 DB：`FLUSHDB` 只允许在 `TASKIQ_REDIS_DB`（默认 2）执行，绝不要在业务 DB `REDIS_DB`（默认 0）执行。

---

## 2. ComfyUI 生成队列（Redis ZSET）

### 2.1 分层调用关系

```text
前端
  │  Bearer token
  ▼
主服务 /api/v1/comfy/*        （鉴权、注入 user_id、透明转发、成功时落资产）
  │  X-Internal-Token
  ▼
muzi-comfy-service /jobs/*    （排队、调度、串行提交 ComfyUI）
  │
  ▼
ComfyUI http://127.0.0.1:8188
```

前端**不直接访问** ComfyUI 网关。主服务从登录态取真实 `user_id` 后转发，因此用户无法伪造身份或代他人提交。

### 2.2 主服务代理接口（前端使用）

前缀：`/api/v1/comfy`，全部需要 `Authorization: Bearer <token>`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/v1/comfy/jobs` | 提交生成任务 |
| `GET` | `/api/v1/comfy/jobs/{job_id}` | 查询任务状态（成功时顺带幂等落库资产） |
| `DELETE` | `/api/v1/comfy/jobs/{job_id}` | 取消排队中的任务 |
| `GET` | `/api/v1/comfy/workflows` | 列出可用工作流 |
| `GET` | `/api/v1/comfy/jobs/{job_id}/files/{filename}` | 读取生成产物文件 |

提交请求：

```json
{
  "workflow_id": "text2image_basic",
  "params": {
    "prompt": "一只在窗边看书的猫",
    "aspect_ratio": "3:4"
  },
  "priority": 5
}
```

`priority` 范围 0~9，数字越小优先级越高，默认 5。

提交响应：

```json
{
  "job_id": "9f2c...",
  "status": "queued",
  "queue_position": 3
}
```

查询响应：

```json
{
  "job_id": "9f2c...",
  "status": "succeeded",
  "workflow_id": "text2image_basic",
  "group": "sdxl",
  "queue_position": null,
  "result": { "prompt_id": "...", "outputs": [ { "filename": "x.png", "kind": "image", "url": "..." } ] },
  "error": null,
  "params": { "prompt": "..." }
}
```

常见错误：

| HTTP | 含义 |
| --- | --- |
| `501 FEATURE_NOT_READY` | 未配置 `INTERNAL_API_TOKEN`，生成能力未启用 |
| `422 INVALID_REQUEST` | 未知 `workflow_id` |
| `429 UPSTREAM_RATE_LIMIT` | 全局队列已满或该用户排队数已达上限 |
| `404 COMFY_JOB_NOT_FOUND` | 任务不存在或不属于当前用户 |
| `409 COMFY_JOB_CONFLICT` | 取消时任务已开始执行 / 读取产物时任务尚未成功 |
| `502 UPSTREAM_ERROR` | 网关或 ComfyUI 不可用 |

### 2.3 网关内部接口（服务间调用）

网关默认监听 `8101`，所有接口要求 `X-Internal-Token` 与 `COMFY_INTERNAL_API_TOKEN` 一致，否则 `401`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/jobs` | 入队，body：`{user_id, workflow_id, params, priority}` |
| `GET` | `/jobs/{job_id}?user_id=...` | 查询状态 |
| `DELETE` | `/jobs/{job_id}?user_id=...` | 取消排队中的任务 |
| `GET` | `/jobs/{job_id}/files/{filename}?user_id=...&subfolder=&type=output` | 读取产出文件 |
| `GET` | `/workflows` | 列出已加载 workflow |

示例：

```bash
curl -X POST http://127.0.0.1:8101/jobs \
  -H "X-Internal-Token: $INTERNAL_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"<uuid>","workflow_id":"text2image_basic","params":{"prompt":"cat"},"priority":5}'
```

### 2.4 调度规则

实现：`muzi-comfy-service/app/scheduler.py`。

- **排序分**：`score = priority * 1e15 + 入队时间戳(ms)`，`ZRANGE` 升序取任务。priority 决定大顺序，同 priority 内 FIFO。
- **模型组亲和**：不写进 score，取任务时按软规则重排，让 ComfyUI 尽量复用驻留模型、减少装卸。
  1. 同组优先：最近提交组的任务往前排。
  2. 连续上限：同组最多连续跑 `COMFY_AFFINITY_MAX_CONTINUOUS`（默认 5）个，之后必须放行其他组，防饥饿。
  3. 兜底：没有异组可跑时继续同组，绝不空转。
- **串行闸门**：`asyncio.Semaphore(1)`，每次只向 ComfyUI 提交一个任务，使排队 / 取消 / 亲和排序在 ComfyUI 之前生效。
- **准入控制**：全局 `COMFY_QUEUE_MAX`（默认 100），每用户 `COMFY_MAX_PER_USER`（默认 5）。
- **显存管理**：交给 ComfyUI 自带的 LRU；网关只负责调度与排队体验。

### 2.5 Redis 键与任务状态

Redis DB：`COMFY_REDIS_DB`（默认 `1`）。

| 键 | 类型 | 说明 |
| --- | --- | --- |
| `comfy:queue` | ZSET | 等待中的任务，member 为 `job_id`，score 见上 |
| `comfy:job:{job_id}` | string(JSON) | 任务完整负载（状态、参数、结果、错误、时间） |
| `comfy:user_pending` | hash | `user_id -> 排队中任务数`，用于每用户限额 |

状态：`queued` → `running` → `succeeded` / `failed`，排队中可 `canceled`。

`queue_position` 为 1-based；排队排名只含等待中的任务，因此会把「正在执行的那一个」算进去，符合「提交 3 个应看到 2、3 位」的直觉。

---

## 3. 短信回执队列（阿里云 MNS）

实现：`app/sms/receipts.py`。该队列用于**接收**短信发送回执，发送验证码不走这里。

### 3.1 配置

| 环境变量 | 说明 |
| --- | --- |
| `SMS_RECEIPT_ENABLED` | 是否启动回执拉取协程，默认 `false` |
| `SMS_MNS_QUEUE_NAME` | 云通信控制台开通回执后获得的队列名；为空则不启动 |
| `SMS_MNS_ENDPOINT` | MNS 接入点 |
| `SMS_MNS_MESSAGE_TYPE` | 默认 `SmsReport` |
| `SMS_ACCESS_KEY_ID` / `SMS_ACCESS_KEY_SECRET` | 调用 dybaseapi 换取 MNS 临时凭证 |

### 3.2 工作方式

1. 启动时若 `SMS_RECEIPT_ENABLED` 且 `SMS_MNS_QUEUE_NAME` 非空，`lifespan` 创建 `run_receipt_loop` 后台协程。
2. 临时凭证临近过期（提前 2 分钟）时，通过 `dybaseapi` 的 `QueryTokenForMnsQueue` 刷新 STS。
3. `batch_receive_message(10, 3)` 批量拉取最多 10 条，解析后批量删除（`batch_delete_message`）。
4. 队列为空（`MessageNotExist`）时等待 10 秒再拉，避免空转。
5. 回执解析支持 JSON 与 Base64 包裹的 JSON；解析失败只记 warning，不影响后续消息处理。

> 该链路是「拉取 + 删除」语义：先记日志再删除。若进程在「已删除但未成功处理」之间崩溃，该条回执会丢失；回执属于旁路观测数据，当前实现接受该取舍。

---

## 4. 配置速查

| 服务 | 环境变量 | 默认值 |
| --- | --- | --- |
| 主服务 | `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` / `REDIS_DB` | `127.0.0.1` / `6379` / 空 / `0` |
| 主服务 | `TASKIQ_REDIS_DB` | `2` |
| Comfy 网关 | `COMFY_REDIS_HOST` / `COMFY_REDIS_PORT` / `COMFY_REDIS_DB` / `COMFY_REDIS_PASSWORD` | `127.0.0.1` / `6379` / `1` / 空 |
| Comfy 网关 | `COMFY_INTERNAL_API_TOKEN` | 空（必须与主服务 `INTERNAL_API_TOKEN` 一致） |
| Comfy 网关 | `COMFY_MAX_PER_USER` / `COMFY_QUEUE_MAX` | `5` / `100` |
| Comfy 网关 | `COMFY_AFFINITY_MAX_CONTINUOUS` / `COMFY_SCAN_CANDIDATES` | `5` / `20` |
| 主服务 | `INTERNAL_API_TOKEN` | 空（为空时 Comfy 能力返回 `501`） |
| 主服务 | `SMS_RECEIPT_ENABLED` / `SMS_MNS_QUEUE_NAME` | `false` / 空 |

### Redis DB 分配约定

| DB | 归属 |
| --- | --- |
| `0` | 主服务业务键（会话、缓存、SSE pub/sub 等） |
| `1` | `muzi-comfy-service` 生成队列与任务负载 |
| `2` | 主服务 Taskiq 内容创作任务队列 |

新增队列时请继续申请独立 DB 或使用独立键前缀，不要与现有约定混用。
