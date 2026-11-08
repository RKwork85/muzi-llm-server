"""Skill 领域错误。message 只写安全说明，不回显技能正文。"""

from __future__ import annotations


class SkillError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class SkillValidationError(SkillError):
    """名称、清单或路径不符合契约。"""


class SkillStateError(SkillError):
    """当前状态不允许该动作。"""


class SkillScopeError(SkillError):
    """作用域尚未接入成员校验，拒绝写入。"""
