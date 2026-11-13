# 状态机

实现在 `app/skills/state.py`。安装状态和激活状态分开。

## 安装

```text
import        imported
register      registered
install       installed
enable        enabled
disable       disabled
enable        enabled
update        保持 enabled 或 disabled
rollback      保持 enabled 或 disabled
uninstall     uninstalled
```

`uninstalled` 不能再启用。`update` 和 `rollback` 只允许发生在 `enabled` 或 `disabled`。

禁用时即使 `active_count > 0` 也允许。已有激活保持原版本，新的激活会被 `can_activate()` 拒绝。卸载在 `active_count > 0` 时返回 `SKILL_IN_USE`。

## 激活

只有 `enabled` 可以打开激活。重复打开返回 `SKILL_ALREADY_ACTIVE`。关闭只接受 `active`，结果是 `deactivated`。关闭不修改 `SkillInstallation.version`。

## 不会进入安装状态的动作

激活和停用不出现在安装表里。这样一次任务启动后固定的版本不会被后来的升级或禁用改掉。
