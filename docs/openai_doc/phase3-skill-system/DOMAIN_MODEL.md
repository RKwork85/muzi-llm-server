# 领域模型

代码在 `app/skills/models.py`。这些对象不是 ORM 模型。

## 标识

| 对象 | 标识前缀 | 说明 |
|---|---|---|
| SkillMetadata / Skill | `sk_` | 技能本身 |
| SkillVersion | 技能标识加版本号 | 版本追加，不覆盖旧摘要 |
| SkillInstallation | `in_` | 某个作用域下的安装 |
| SkillActivation | `ac_` | 一次任务固定的版本 |
| SkillExecution | `ex_` | 一次执行结果 |
| SkillResource | 技能、版本、相对路径 | 引用、素材或脚本索引 |

## 字段边界

`SkillMetadata` 只含发现所需的名称、说明、版本、作用域、状态和来源。`SkillManifest.instruction_sha256` 指向文件摘要，`body_bytes` 只记录长度。`LoadedInstructions.text` 有 8000 字上限，并固定 `untrusted=True`。

`SkillPermission.declared_tools` 是技能自己写下的工具名。模型里没有 handler，也没有授权字段。

`SkillContext` 携带当前激活列表、模式和字符预算，供现有 `ContextManager` 或对话提示使用。

## 作用域和来源

枚举保留 `system`、`organization`、`user`、`project`。`assert_scope_writable()` 只放行 `system` 和 `user`。来源保留 `remote`，`assert_source_enabled()` 只放行内置、本地目录和 ZIP。

## 与旧提示词的关系

`app/prompts/skills.py` 的四条字符串不是这些模型。M4 之前对话仍走旧函数。
