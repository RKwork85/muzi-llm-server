"""安装状态和激活状态。激活不改写安装版本。"""

from __future__ import annotations

from app.skills.errors import SkillScopeError, SkillStateError
from app.skills.models import (
    ENABLED_SOURCES,
    WRITABLE_SCOPES,
    ActivationStatus,
    Skill,
    SkillAction,
    SkillScope,
    SkillSourceKind,
    SkillStatus,
)

_NEXT: dict[tuple[SkillStatus | None, SkillAction], SkillStatus] = {
    (None, SkillAction.IMPORT): SkillStatus.IMPORTED,
    (SkillStatus.IMPORTED, SkillAction.REGISTER): SkillStatus.REGISTERED,
    (SkillStatus.REGISTERED, SkillAction.INSTALL): SkillStatus.INSTALLED,
    (SkillStatus.INSTALLED, SkillAction.ENABLE): SkillStatus.ENABLED,
    (SkillStatus.ENABLED, SkillAction.DISABLE): SkillStatus.DISABLED,
    (SkillStatus.DISABLED, SkillAction.ENABLE): SkillStatus.ENABLED,
    (SkillStatus.ENABLED, SkillAction.UPDATE): SkillStatus.ENABLED,
    (SkillStatus.DISABLED, SkillAction.UPDATE): SkillStatus.DISABLED,
    (SkillStatus.ENABLED, SkillAction.ROLLBACK): SkillStatus.ENABLED,
    (SkillStatus.DISABLED, SkillAction.ROLLBACK): SkillStatus.DISABLED,
    (SkillStatus.REGISTERED, SkillAction.UNINSTALL): SkillStatus.UNINSTALLED,
    (SkillStatus.INSTALLED, SkillAction.UNINSTALL): SkillStatus.UNINSTALLED,
    (SkillStatus.ENABLED, SkillAction.UNINSTALL): SkillStatus.UNINSTALLED,
    (SkillStatus.DISABLED, SkillAction.UNINSTALL): SkillStatus.UNINSTALLED,
}


def assert_scope_writable(scope: SkillScope) -> None:
    if scope not in WRITABLE_SCOPES:
        raise SkillScopeError("SKILL_SCOPE_UNAVAILABLE", "该作用域还不能注册技能")


def assert_source_enabled(kind: SkillSourceKind) -> None:
    if kind not in ENABLED_SOURCES:
        raise SkillStateError("SKILL_SOURCE_UNAVAILABLE", "该来源还不能导入")


def transition(status: SkillStatus | None, action: SkillAction, *, active_count: int) -> SkillStatus:
    if active_count < 0:
        raise SkillStateError("SKILL_STATE_INVALID", "激活计数无效")
    if action == SkillAction.UNINSTALL and active_count > 0:
        raise SkillStateError("SKILL_IN_USE", "仍有任务在使用该技能")
    key = (status, action)
    if key not in _NEXT:
        raise SkillStateError("SKILL_STATE_INVALID", "当前状态不能执行该动作")
    return _NEXT[key]


def can_activate(skill: Skill) -> None:
    if skill.metadata.status != SkillStatus.ENABLED:
        raise SkillStateError("SKILL_NOT_ENABLED", "技能未启用")


def next_activation(current: ActivationStatus | None) -> ActivationStatus:
    if current == ActivationStatus.ACTIVE:
        raise SkillStateError("SKILL_ALREADY_ACTIVE", "该任务已经激活此技能")
    return ActivationStatus.ACTIVE


def close_activation(current: ActivationStatus) -> ActivationStatus:
    if current != ActivationStatus.ACTIVE:
        raise SkillStateError("SKILL_NOT_ACTIVE", "技能不在激活中")
    return ActivationStatus.DEACTIVATED
