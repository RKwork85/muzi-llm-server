# 05 · 数据契约与信息流

本文件给出逻辑契约，不强制照抄字段。Cursor 必须先检查现有 Pydantic 模型、领域协议、数据库模型和服务接口，复用已存在类型。

## 1. DiscoveryRequest（逻辑字段）

- `task_text`: 当前任务文本
- `surface`: `chat | agent`
- `mode`: `AUTO | RECOMMENDED | EXPLICIT | DISABLED`
- `explicit_skill_ids`: 可选明确选择
- `max_candidates`: 有上限
- `context_hints`: 可选、非权威的任务上下文

身份字段由服务端 principal 注入，不允许客户端通过请求体覆盖。

## 2. Candidate（逻辑字段）

- `skill_id`
- `name`
- `description`
- `version`
- `score`
- `reason_codes`
- `matched_fields`
- `availability`
- `requires_confirmation`

候选不包含正文、脚本内容或其他用户数据。

## 3. LoadedSkill（逻辑字段）

- `skill_id`
- `version`
- `content_digest`
- `frontmatter`
- `instruction_body`
- `loaded_at`
- `owner_scope`

仅由 Loader 在选择后产生。不要把这个对象用于 Discovery 列表响应。

## 4. ActivationSnapshot（逻辑字段）

- `activation_id`
- `task_id`
- `principal_id`
- `skill_id`
- `skill_version`
- `content_digest`
- `status`
- `created_at`
- `updated_at`

必须确保任务与用户绑定，不能通过猜测 activation_id 访问别人的记录。

## 5. RuntimeContext（逻辑字段）

- `task_id`
- `surface`
- `system_sections`
- `conversation_sections`
- `user_task`
- `skill_sections`
- `resource_sections`
- `budget`
- `trace_metadata`

Skill sections 必须被标记为非可信内容，且不能覆盖系统策略。

## 6. 信息流关键断言

- Discovery 不调用 `read_skill_body`。
- Selection 未确认时不创建激活记录。
- Loader 在读正文前重新校验权限与状态。
- Activation Snapshot 保存摘要与版本。
- Context Builder 只接受已校验的 LoadedSkill。
- Chat/Agent 不直接访问文件系统。
- API 不直接拼接 prompt。
- Resource Resolver 不允许越出 Skill 包根目录。
- 所有失败都有明确错误码和安全日志。

## 7. 建议错误类别

复用项目已有错误响应体系；缺失时再补充：
- `SKILL_NOT_FOUND`
- `SKILL_NOT_ACCESSIBLE`
- `SKILL_NOT_AVAILABLE`
- `SKILL_VERSION_CONFLICT`
- `SKILL_PACKAGE_INVALID`
- `SKILL_CONTENT_TOO_LARGE`
- `SKILL_RESOURCE_NOT_ALLOWED`
- `SKILL_DISCOVERY_FAILED`
- `SKILL_ACTIVATION_CONFLICT`
- `SKILL_RUNTIME_FAILED`
- `SKILL_CONTEXT_BUDGET_EXCEEDED`
- `SKILL_TASK_CANCELLED`

错误响应不得暴露其他用户 Skill 是否存在。
