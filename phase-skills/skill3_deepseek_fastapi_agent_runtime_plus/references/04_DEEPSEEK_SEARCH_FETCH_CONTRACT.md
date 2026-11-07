# 04 DeepSeek Search / Fetch 官方契约

来源（2026-11-06 核验）：

- 指南：https://api-docs.deepseek.com/zh-cn/guides/search
- Search 接口：https://api-docs.deepseek.com/zh-cn/api/search
- Fetch 接口：https://api-docs.deepseek.com/zh-cn/api/fetch

> 文档可能更新。实施 T2 前，若有联网能力，必须重新读取以上三页并与本文对照，差异写入 `docs/phase3/DEVIATIONS.md`；若无法联网核对，在文档中标注"未对照最新官方文档"。不得凭记忆补充字段。

## 1. 基本信息

| 项 | 内容 |
|---|---|
| Base URL | `https://api.deepseek.com`（以项目现有配置为准，先读真实 Settings，不要假设与 Chat Completions 的 base_url 写法相同） |
| Search | `POST /v1/tools/search` |
| Fetch | `POST /v1/tools/fetch` |
| 鉴权 | `Authorization: Bearer <DeepSeek API Key>`，`Content-Type: application/json`，**与对话接口使用同一个 Key** |
| 形式 | 原始 JSON API，不走 OpenAI SDK；用 `httpx.AsyncClient` |
| 只读 | 官方说明 Search 和 Fetch 都是只读请求 |
| 未知请求字段 | 官方说明请求体中的未知字段会被忽略（所以拼错字段名不会报错，必须靠测试断言请求体） |

## 2. Search 请求

| 字段 | 类型 | 必填 | 默认 | 约束 |
|---|---|---|---|---|
| `query` | string | 是 | - | 1–128 个 Unicode 字符，不能全为空白；搜索语法原样传给搜索服务 |
| `count` | integer | 否 | 10 | 1–50 |
| `ori_question` | string \| null | 否 | - | 1–128 字符，用户原始问题，有助于理解意图 |
| `localization` | string | 否 | `""` | 用于优先本地化结果的 **IP 地址** |
| `need_content` | boolean | 否 | false | true 时才在 `content` 返回正文；false 时 `content` 恒为空字符串 |

支持的搜索语法：`site:`、`-site:`、`-词`、`-"短语"`、`"精确短语"`、`lang:`、`after:`、`before:`。

## 3. Search 响应

`results`: 数组，无匹配时为空数组（HTTP 200）。每项：

| 字段 | 类型 | 说明 |
|---|---|---|
| `rerank_score` | number 0–3 | ≥ 1.5 为优质结果 |
| `title` | string | 标题 |
| `url` | string | 链接 |
| `snippet` | string | 短摘要，通常 100–200 字符，适合前端展示 |
| `summary` | string | 长摘要，通常 600–800 字符，**适合给模型阅读** |
| `content` | string | 网页正文；`need_content=false` 或缺失时为空字符串 |
| `site_name` | string \| null | |
| `site_icon` | string \| null | |
| `date` | string \| null | UTC RFC 3339，未知为 null |
| `authority` | integer -1..4 | -1 未知；≥ 3 为极权威 |

## 4. Fetch

请求：`{"url": "<string>"}`。`url` 非空、≤ 8192 字符、必须是含 host 的 HTTP/HTTPS URL、不能携带用户名或密码。

响应：`{"title": "<string，缺失为空串>", "content": "<Markdown 正文>"}`。**响应里没有 final_url、status、content_type**，不得在工具结果中编造这些字段。

官方说明 Fetch 结果受目标站点的可用性和访问策略影响。

## 5. 错误

错误体为 OpenAI 兼容格式：`{"error": {"message", "type", "param": null, "code"}}`。

| HTTP | 官方含义 | 本项目分类（建议名，沿用现有错误体系） | 可重试 |
|---|---|---|---|
| 400 | 请求头/体不合法 | 我们的请求构造有缺陷 → `web_invalid_request` | 否 |
| 401 | Key 缺失或无效 | `web_auth_error` | 否 |
| 402 | 余额不足 | `web_balance_error`（与现有 `UPSTREAM_BALANCE` 归类一致） | 否 |
| 403 | Fetch 被安全策略拒绝 | `web_fetch_blocked` | 否 |
| 404 | Fetch 未能获取可用内容（URL 不可用、被站点拦截、站点返回错误、类型不支持、无可提取正文） | `web_fetch_unavailable` | 否 |
| 408 | 读取请求体超时 | `web_timeout` | 是（官方建议等待后重试） |
| 413 | 请求体过大 | `web_invalid_request` | 否 |
| 429 | 超过账号级 QPS；Fetch 另有目标域名级 QPS | `web_rate_limited` | 是 |
| 500 | 服务器内部错误 | `web_upstream_error` | 是 |
| 504 | 下游请求超时 | `web_timeout` | 是 |

官方对重试的说明：Search/Fetch 是只读请求；408、429、5xx 等待后可重试；402 先充值；其他 4xx 不改请求就不要重试。

- 不得把上游 `error.message` 原文原样塞进模型上下文或事件（可能含请求细节）；只返回分类和固定的安全文案。
- 网络层异常（连接失败、读超时、被取消）另行映射：连接失败 → `web_network_error`；超时 → `web_timeout`；取消必须传播 `CancelledError`。

## 6. 计费与限速

- 计费：按**成功请求次数**计费，不按 token 或结果条数；Search 5 元/千次，Fetch 5 元/千次（官方文档当前数值，可能变化）。只有 HTTP 200 且产生结果才计费（Search `results` 非空，Fetch 返回了正文）；失败、无结果、超时不计费。`count` 不影响单次价格。
- 限速（按账号，与用哪个 Key 无关，且与模型并发限制独立）：Search 100 QPS，Fetch 25 QPS；Fetch 另有按目标域名的 QPS 限制。
- 含义：每次工具调用都有真实费用和额度消耗，所以 `max_calls_per_run`、并发闸门不是可选项。
