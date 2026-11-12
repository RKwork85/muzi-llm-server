"""验证码只以摘要形式放在 Redis，并限制发送间隔和错误次数。"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from app.core.exceptions import AuthFailedError, DatabaseUnavailableError, SmsRateLimitError

_MAX_ATTEMPTS = 5


def new_code(length: int) -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(length))


def code_digest(phone: str, code: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), f"{phone}:{code}".encode(), hashlib.sha256).hexdigest()


class SmsCodeStore:
    def __init__(self, redis, *, ttl_seconds: int, cooldown_seconds: int, pepper: str) -> None:
        if redis is None:
            raise DatabaseUnavailableError("验证码服务暂不可用")
        self.redis = redis
        self.ttl_seconds = ttl_seconds
        self.cooldown_seconds = cooldown_seconds
        self.pepper = pepper

    async def reserve(self, scene: str, phone: str) -> None:
        ok = await self.redis.set(self._cooldown_key(scene, phone), "1", nx=True, ex=self.cooldown_seconds)
        if not ok:
            raise SmsRateLimitError()

    async def save(self, scene: str, phone: str, code: str) -> None:
        digest = code_digest(phone, code, self.pepper)
        await self.redis.set(self._code_key(scene, phone), digest, ex=self.ttl_seconds)
        await self.redis.delete(self._attempt_key(scene, phone))

    async def rollback(self, scene: str, phone: str) -> None:
        await self.redis.delete(self._cooldown_key(scene, phone), self._code_key(scene, phone))

    async def verify(self, scene: str, phone: str, code: str) -> None:
        stored = await self.redis.get(self._code_key(scene, phone))
        if not stored:
            raise AuthFailedError("验证码错误或已失效")
        attempts = int(await self.redis.incr(self._attempt_key(scene, phone)))
        await self.redis.expire(self._attempt_key(scene, phone), self.ttl_seconds)
        if attempts > _MAX_ATTEMPTS:
            await self.redis.delete(self._code_key(scene, phone), self._attempt_key(scene, phone))
            raise AuthFailedError("验证码错误次数过多，请重新获取")
        expected = code_digest(phone, code, self.pepper)
        if not hmac.compare_digest(str(stored), expected):
            raise AuthFailedError("验证码错误或已失效")
        await self.redis.delete(self._code_key(scene, phone), self._attempt_key(scene, phone))

    @staticmethod
    def _code_key(scene: str, phone: str) -> str:
        return f"sms:code:{scene}:{phone}"

    @staticmethod
    def _cooldown_key(scene: str, phone: str) -> str:
        return f"sms:cooldown:{scene}:{phone}"

    @staticmethod
    def _attempt_key(scene: str, phone: str) -> str:
        return f"sms:attempts:{scene}:{phone}"
