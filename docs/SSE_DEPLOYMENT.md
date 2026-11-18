# 实时推送（SSE）部署说明

充值到账后前端自动更新额度，靠的是一条登录态 SSE 长连接：

| 项 | 值 |
|---|---|
| 端点 | `GET /api/v1/me/events` |
| 响应类型 | `text/event-stream` |
| 鉴权 | `Authorization: Bearer <token>` |
| 事件名 | `quota.credited` |
| 心跳 | 每 15 秒一帧 `: ping`（注释帧） |
| 实现 | `app/services/recharge_events.py`、`app/api/v1/endpoints/me.py` |

**长连接会引入几项普通请求没有的资源约束。新增或迁移部署机器时，请逐条确认本文档。**

---

## 1. 文件描述符上限（最容易遗漏，且会导致上线即故障）

### 为什么

Linux 中 socket 也是文件，每条连接占 1 个 fd。普通接口「借用后立即归还」，同时占用的 fd 只有并发请求数；SSE 是长期的，**同时占用的 fd 等于在线页面数**。

系统默认 `ulimit -n` 是 **1024**（soft limit），按本项目的实际开销估算：

| 占用方 | fd 数 |
|---|---|
| 标准输入输出 + epoll + 监听 socket | ~6 |
| 数据库连接池（`DB_POOL_SIZE=5` + `DB_MAX_OVERFLOW=10`） | 最多 15 |
| Redis 连接池（`REDIS_MAX_CONNECTIONS=20`，含 1 条 psubscribe 桥接） | 最多 20 |
| 日志、临时文件等 | ~10 |
| **基础开销合计** | **~51** |
| **剩余可给 SSE** | **~970** |

即约 970 个页面同时打开后，服务端开始**拒绝新连接**（`OSError: [Errno 24] Too many open files`）。注意此时：

- CPU、内存、数据库监控全部正常
- 已建立的 SSE 连接仍然工作
- 只有新请求失败

这种「部分用户坏、部分用户好」且监控无异常的现象极难定位，因此必须在部署前确认。

### 怎么确认

查当前 shell：

```bash
ulimit -n          # soft limit
ulimit -Hn         # hard limit（soft 不能超过它）
```

查**正在运行的那个进程**的实际值（比查 shell 更准）：

```bash
cat /proc/<uvicorn_pid>/limits | grep "open files"
```

### 怎么修

临时（只影响当前 shell 及子进程）：

```bash
ulimit -n 65535 && python -m uvicorn app.main:app --host 0.0.0.0 --port 8001
```

systemd 托管：

```ini
[Service]
LimitNOFILE=65535
```

`/etc/security/limits.conf`（需重新登录或重启服务才生效，对已有会话无效）：

```
*  soft  nofile  65535
*  hard  nofile  65535
```

若前面有 nginx，**nginx worker 是另一个独立的 fd 上限**，需同时设置 `worker_rlimit_nofile`。

### 推荐做法

写进启动脚本，把不确定性固定在仓库里，不依赖运维记忆：

```bash
#!/usr/bin/env bash
set -e
# SSE 长连接：每个在线页面占 1 个 fd，系统默认 1024 不够
ulimit -n 65535
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8001
```

> 这里刻意不加 `--workers`，原因见 [4. 多进程部署](#多进程部署)：Agent 任务目前在进程内存储。

**Windows 上没有 `ulimit` 机制（用内核 handle，上限百万级），因此本地开发测不出这个问题。不要以本地表现推断 Linux 生产行为。**

---

## 2. 反向代理不得缓冲响应

代理默认会攒够缓冲区才下发，SSE 会因此失去实时性（事件被积压，直到攒满或连接结束）。

服务端已在响应头声明豁免：

```python
headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
```

`X-Accel-Buffering: no` 是 nginx 识别的专用头，**默认配置下无需改 nginx 即可正常工作**，这也是本项目选择该方案的原因。

但有两个例外必须人工确认：

| 情况 | 说明 |
|---|---|
| nginx 里显式写了 `proxy_buffering on;` | 显式配置优先级高于响应头，需要改成 `off` 或加例外 |
| 前置是云厂商 LB / 其他网关 | 未必识别该头，需按厂商文档关闭缓冲 |

超时方面，nginx 默认 `proxy_read_timeout 60s`，而心跳间隔是 15 秒，**默认值足够**（心跳会刷新空闲计时器）。只有当运维把该值调到 15 秒以下才会误杀连接。

开发环境的 Vite 代理（`vite.config.js` 中 `/api` → `127.0.0.1:8001`）底层流式透传、不缓冲，已实测通过。

**注意：本仓库没有任何 nginx 配置文件**。生产网关配置不在版本控制内，部署时需单独确认上述两点。

---

## 3. 优雅关闭（必须保留信号钩子）

### 问题

uvicorn 的关闭顺序是：

```
停止接受新连接 → 等待在途响应结束 → 才执行 lifespan shutdown
```

SSE 响应永远不会自然结束，进程会永久停在：

```
Waiting for connections to close. (CTRL+C to force quit)
```

此时端口仍在监听，但**一个请求都不再处理**，前端所有请求无限 pending（表现为整页卡死）。而 lifespan 里的清理排在第二步之后，放在那里来不及。

`uvicorn.Config.timeout_graceful_shutdown` 默认为 `None`，即**无限等待**，所以不能靠它兜底。

### 方案

进程在信号阶段就主动打断所有流，再把控制权交还 uvicorn：

| 文件 | 作用 |
|---|---|
| `app/services/shutdown.py` | 接管 `SIGINT`/`SIGTERM`，先调 `close_recharge_events()`，再委托原 uvicorn 处理器 |
| `app/services/recharge_events.py` | `close_all()` 置关闭标记并唤醒所有订阅者；`subscribe()` 循环条件检查该标记，立即退出生成器 |

由 `app/main.py` 的 lifespan 启动时安装（幂等、仅主线程、保留原处理器链）。`reset()` 保证热重载 / 测试中重复进入 lifespan 后仍可订阅。

### 注意

- **不要移除 `install_shutdown_hook()`**，否则 `Ctrl+C` 或服务重启会卡死。
- 生产环境不应使用 `--reload`：热重载会踢掉所有在线连接，且 reload 的关闭路径与优雅关闭不同，容易暴露问题。

---

## 4. 连接与资源模型

这三类资源都不随连接数放大，这是设计目标：

| 资源 | 每条 SSE 连接的占用 | 是否随连接数增长 |
|---|---|---|
| 数据库连接 | **0**（走专用 `get_stream_user_id`，取到 `user_id` 立刻关闭 session） | 不增长 |
| Redis 连接 | **0**（全进程共用 1 条 `psubscribe`） | 不增长 |
| 进程内内存 | 1 个 `asyncio.Queue(maxsize=32)` + 1 个 Task，约 6–12 KB | 线性，但极轻 |
| 内核 socket 缓冲 | ~16 KB | 线性（不占进程 RSS） |

### 为什么数据库连接必须是 0

普通接口用 `get_authenticated_user`，它借的是**请求级** session，事务要等响应结束才提交归还。SSE 响应在客户端断开前永不结束，若沿用它，一条池子连接会被整条流的生命周期占住。池子只有 `5 + 10`，几个页签就能抽干，之后**所有**请求卡在等连接上（前端表现为整页卡死）。

因此 SSE 专用 `app/api/deps.py:get_stream_user_id`：自己开短生命周期 session，取到 id 立即关闭。

**新增任何 SSE 端点时必须复用该依赖，不要用 `get_authenticated_user`。**

### 为什么 Redis 只有 1 条连接

`_bridge_task` 是**进程级全局单例**，首次有订阅者时惰性启动，用 `psubscribe("muzi:recharge:events:*")` 一次性订阅所有用户频道，收消息后在内存里按 `user_id` 扇出到各自的 Queue。

若改成「每个用户各订阅自己的频道」，Redis 连接数就等于在线用户数，会立刻撞上 `REDIS_MAX_CONNECTIONS=20`，并且会连带把验证码、缓存等共用该客户端的功能一起拖垮。

### 多进程部署

`--workers N` 时每个 worker 是独立进程，各自有独立的 Redis 池和 1 条桥接连接：

| worker 数 | psubscribe 连接数 | 每 worker 的 Redis 池上限 |
|---|---|---|
| 1 | 1 | 20 |
| 4 | 4 | 各 20 |
| 16 | 16 | 各 20 |

`REDIS_MAX_CONNECTIONS=20` 不跨进程共享，16 个 worker 不需要 320 条连接。

跨进程投递依赖 Redis pub/sub；事件信封带 `process` 字段（进程随机 id），桥接收到自己发出的回声时丢弃，避免同一事件推两次。

> ⚠️ **本项目当前必须单 worker。** SSE 的跨进程投递已经就绪，但 Agent 任务仍保存在进程内 `TaskManager._tasks` 字典中（`app/agents/task_manager.py`）：任务创建在 A 进程，前端轮询或取消打到 B 进程会查不到，表现为「任务不存在」。横向扩展前必须先给 Agent 任务引入共享存储，不能只加 `--workers`。

---

## 5. 容量参考

单进程（Python 单核）的粗略上限：

| 场景 | 单进程可支撑的在线页面 |
|---|---|
| 只有 SSE 挂机，几乎无其他请求 | 1–3 万 |
| SSE + 少量普通请求 | 5 千–1 万 |
| SSE + 大量 LLM 流式对话 | **1 千–3 千**（业务先耗尽 CPU） |

真实瓶颈是单进程只能用一个 CPU 核，且该核要与 LLM 流式对话共享，**不是 SSE 本身**。

心跳是唯一随连接数线性增长的 CPU 项：每 15 秒每连接唤醒一次，即每秒唤醒次数 = 连接数 ÷ 15。1 万连接约 667 次/秒，占单核个位数百分比。

**`HEARTBEAT_SECONDS` 不要随意调大**：它同时决定服务重启时「发现连接已断开」的最大延迟，而 `close_all()` 的兜底依赖这一点。

扩容顺序（前三步都不改代码）：

1. `ulimit -n 65535` —— 消除 1024 硬上限
2. 去掉 `--reload` —— 生产重启不再踢掉所有连接
3. 调大 `HEARTBEAT_SECONDS`（30–45s）—— 注意上一段的副作用
4. `--workers N`（N ≈ CPU 核数）—— CPU 横向扩展；**需先解决 Agent 任务的进程内存储，见上一节警告**

单 worker 是当前默认且安全的形态。`ulimit` 放宽到 65535 后，fd 不再是首个触顶点（可支撑约 7000 条连接），瓶颈回到 CPU——即上表中的 1–3 千在线页面（SSE 与 LLM 流式对话共用单核）。

---

## 6. 客户端行为（前端）

实现：`muzi-llm-frontend/src/services/meEvents.js`、`src/api/events.js`

| 行为 | 说明 |
|---|---|
| 连接时机 | `MainLayout` 挂载时 `startMeEvents()`（全局单例），卸载时断开 |
| 单例约束 | `if (running) return`，多页签不会叠加连接 |
| 断线重连 | 指数退避 1s → 30s 封顶，连上即重置 |
| 401 / 403 | **不重连**（登录已失效，重连只会刷失败），等下次登录重启 |
| 页面切后台 | `openWhenHidden: true`，不被浏览器挂起 |
| 库内重试 | `onerror` 主动抛出，禁掉 `fetch-event-source` 的无限重试，避免与上层退避叠加 |
| 兜底 | 充值页在 SSE 断开时启用 5 秒轮询，SSE 恢复即自停 |

因为推送是「尽力而为」（单用户积压超 32 条会丢弃），到账保障实际有三层：SSE 实时推送 → 充值页轮询 → 手动「我已付款」查单。

**已知缺口**：断线期间的事件不会补发（未实现 `Last-Event-ID` 续传）。若用户不在充值页，重启期间的到账需等下次额度刷新才更新。修法是在重连成功且非首次连接时调一次 `me.loadQuota()` 对账。

---

## 7. 上线自检

按顺序确认：

```bash
# 1. fd 上限（关键）
cat /proc/$(pgrep -f "uvicorn app.main")/limits | grep "open files"
#    期望：65535，若为 1024 则在线约 970 页面后新请求全部失败

# 2. 端点可达且返回正确类型（401 说明服务正常、仅缺登录态）
curl -i -H "Accept: text/event-stream" http://127.0.0.1:8001/api/v1/me/events
#    期望：HTTP 401 + LOGIN_REQUIRED（未带 token 时）

# 3. 心跳是否真的在流（带 token 后观察 15 秒）
curl -N -H "Authorization: Bearer <token>" http://127.0.0.1:8001/api/v1/me/events
#    期望：立即 ": connected"，之后每 15 秒一条 ": ping"

# 4. 优雅关闭不会卡住
#    按 Ctrl+C 后应正常退出，不应停在 "Waiting for connections to close"
```

经过代理时（如 `https://<域名>/api/v1/me/events`）重复第 3 步：心跳能按 15 秒间隔准时到达，说明代理没有缓冲。

---

## 8. 故障现象速查

| 现象 | 可能原因 | 排查 |
|---|---|---|
| 付款后页面不自动更新，但手动查询能查到 | SSE 断开或代理缓冲 | 充值页圆点是否变灰；`curl -N` 看心跳间隔 |
| 前端「整页卡死」，所有请求 pending | SSE 占满数据库连接池 | 确认 SSE 端点是否误用了 `get_authenticated_user` |
| 服务重启卡在 `Waiting for connections to close` | 信号钩子未安装 | 确认 `install_shutdown_hook()` 在 lifespan 中 |
| 服务重启后所有请求失败，但老连接正常 | fd 到顶（1024） | `/proc/<pid>/limits` 查 `open files`；`ls /proc/<pid>/fd \| wc -l` |
| 上线第 21 个用户后实时推送失效 | Redis 池被打满 | 确认是否为每用户各订阅（应共用一个 psubscribe） |
| 心跳不来、断线频繁 | 代理 `proxy_read_timeout` < 15s | 查网关配置 |
| 登录过期后每秒一串失败请求 | 401 未停止重连 | 确认 `meEvents.js` 的 `unauthorized` 分支 |
