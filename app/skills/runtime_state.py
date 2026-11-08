"""运行阶段。安装状态仍由 app.skills.state 管理。"""

from __future__ import annotations

from enum import Enum

from app.skills.errors import SkillStateError


class RuntimePhase(str, Enum):
    CREATED = "created"
    RESOLVING = "resolving"
    LOADING = "loading"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_PHASES = frozenset({RuntimePhase.COMPLETED, RuntimePhase.FAILED, RuntimePhase.CANCELLED})

_NEXT = {
    (RuntimePhase.CREATED, "resolve"): RuntimePhase.RESOLVING,
    (RuntimePhase.RESOLVING, "load"): RuntimePhase.LOADING,
    (RuntimePhase.LOADING, "activate"): RuntimePhase.ACTIVE,
    (RuntimePhase.ACTIVE, "complete"): RuntimePhase.COMPLETED,
    (RuntimePhase.CREATED, "fail"): RuntimePhase.FAILED,
    (RuntimePhase.RESOLVING, "fail"): RuntimePhase.FAILED,
    (RuntimePhase.LOADING, "fail"): RuntimePhase.FAILED,
    (RuntimePhase.ACTIVE, "fail"): RuntimePhase.FAILED,
    (RuntimePhase.CREATED, "cancel"): RuntimePhase.CANCELLED,
    (RuntimePhase.RESOLVING, "cancel"): RuntimePhase.CANCELLED,
    (RuntimePhase.LOADING, "cancel"): RuntimePhase.CANCELLED,
    (RuntimePhase.ACTIVE, "cancel"): RuntimePhase.CANCELLED,
}


def move(phase: RuntimePhase, action: str) -> RuntimePhase:
    if phase in TERMINAL_PHASES:
        raise SkillStateError("SKILL_STATE_INVALID", "运行已经结束")
    key = (phase, action)
    if key not in _NEXT:
        raise SkillStateError("SKILL_STATE_INVALID", "当前状态不能执行该动作")
    return _NEXT[key]
