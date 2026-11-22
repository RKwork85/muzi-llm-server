"""短信验证码存储。键与摘要格式保持历史行为，逻辑委托给通用实现。"""

from __future__ import annotations

from app.verification.codes import VerificationCodeStore, code_digest as _code_digest
from app.verification.codes import new_code

__all__ = ["SmsCodeStore", "code_digest", "new_code"]


def code_digest(phone: str, code: str, pepper: str) -> str:
    """短信摘要输入为 ``{phone}:{code}``，与通用实现的空前缀分支一致。"""
    return _code_digest(phone, code, pepper)


class SmsCodeStore:
    def __init__(self, redis, *, ttl_seconds: int, cooldown_seconds: int, pepper: str) -> None:
        # namespace=sms、无摘要前缀：键仍是 sms:code:... ，旧验证码继续有效。
        self._store = VerificationCodeStore(
            redis,
            namespace="sms",
            ttl_seconds=ttl_seconds,
            cooldown_seconds=cooldown_seconds,
            pepper=pepper,
        )
        self.redis = redis

    async def reserve(self, scene: str, phone: str) -> None:
        await self._store.reserve(scene, phone)

    async def save(self, scene: str, phone: str, code: str) -> None:
        await self._store.save(scene, phone, code)

    async def rollback(self, scene: str, phone: str) -> None:
        await self._store.rollback(scene, phone)

    async def verify(self, scene: str, phone: str, code: str) -> None:
        await self._store.verify(scene, phone, code)
