# M3 设计

发现服务是 `app/skills/discovery.py` 的 `SkillDiscoveryService`。它只调用 `FileSkillRegistry.query()`，读取索引 JSON，不打开 `SKILL.md`。

## 模式

| 模式 | 行为 |
|---|---|
| disabled | 不查询注册表 |
| recommended | 返回达到 0.4 的候选，不选择、不加载 |
| auto | 名称精确匹配达到 0.96，且没有含糊冲突时，给出选择建议 |
| explicit | 按技能标识或名称查找，仍检查所有权和启用状态 |

分数来自名称、标签、分类和说明里的词，范围是 0 到 1。同分按名称和技能标识排序。两个非精确名称的高分候选相差小于 0.05 时，只推荐，不自动选择。

身份使用调用方传入的 `SkillActor`。别的用户的技能不会进入候选。看不到的显式指定返回 `SKILL_NOT_FOUND`。看得到但未启用返回 `SKILL_NOT_AVAILABLE`。

## 加载

`SkillPackageLoader` 在选择之后才读文件。它重新检查启用状态、版本摘要和包目录是否仍在注册表根下。摘要不一致返回 `SKILL_VERSION_CONFLICT`。文件损坏返回 `SKILL_PACKAGE_INVALID`，不把半截正文交给上下文。

## 旧提示

`research`、`polish`、`minutes`、`weekly` 仍由 `app/prompts/skills.py` 的 `skill_notes()` 提供。注册表里的同名技能不会替换这四条提示。
