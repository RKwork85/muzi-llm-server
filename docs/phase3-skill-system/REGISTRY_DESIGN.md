# 注册表

实现是 `app/skills/registry.py` 的 `FileSkillRegistry`。清单解析在 `app/skills/frontmatter.py`。

## 存储

根目录由调用方传入，不放进数据库。

```text
records/sk_xxx.json
packages/sk_xxx/1.0.0/SKILL.md
packages/sk_xxx/1.0.0/references/
```

`records` 只保存名称、说明、版本、作用域、状态、来源、所有者和摘要。正文留在 `packages`。

## 行为

- `register` 同时完成导入和登记，状态落到 `registered`。同作用域、同所有者、同名且尚未卸载时返回 `SKILL_NAME_CONFLICT`。
- 用户技能的 `owner_id` 来自调用方。别的用户查询或读取时得到 `SKILL_NOT_FOUND`。
- 系统技能只有 `allow_system=True` 的注册表可以写入，所有者为空，任何已登录身份都可以看见元数据。
- 组织和项目作用域仍然拒绝写入。
- `import_zip` 接受根目录或单层目录里的 `SKILL.md`。压缩包总大小不超过 1 MB。`..` 和绝对路径会被拒绝。`references`、`assets`、`scripts` 会复制进包目录，脚本不会执行。
- `scan_directory` 递归查找 `SKILL.md`，跳过包外路径。
- 更新只允许发生在启用或禁用之后，并保留旧版本文件。
- 有激活计数时不能卸载。

没有新增第三方包。远程来源仍由 `assert_source_enabled()` 拒绝。
