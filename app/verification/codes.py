"""与渠道无关的验证码存储。

验证码只以 HMAC 摘要形式放在 Redis，并限制发送间隔与错误次数。
短信与邮箱各自用一个 namespace，键与摘要互不干扰：

- 短信：``sms:code:{scene}:{phone}``，摘要输入 ``{phone}:{code}``
- 邮箱：``email:code:{scene}:{email}``，摘要输入 ``email:{email}:{code}``

摘要输入沿用短信现有格式（不加 namespace 前缀），是为了让已发出的短信验证码
在本次改造后仍能校验通过。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from app.core.exceptions import AuthFailedError, DatabaseUnavailableError, CodeRateLimitError

_MAX_ATTEMPTS = 5


def new_code(length: int) -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(length))


def code_digest(target: str, code: str, pepper: str, *, prefix: str = "") -> str:
    """计算验证码摘要。prefix 只用于区分渠道，短信渠道保持空前缀。"""
    return hmac.new(
        pepper.encode(),
        f"{prefix}{target}:{code}".encode(),
        hashlib.sha256,
    ).hexdigest()


class VerificationCodeStore:
    def __init__(
        self,
        redis,
        *,
        namespace: str,
        ttl_seconds: int,
        cooldown_seconds: int,
        pepper: str,
        digest_prefix: str = "",
    ) -> None:
        if redis is None:
            raise DatabaseUnavailableError("验证码服务暂不可用")
        self.redis = redis
        self.namespace = namespace
        self.ttl_seconds = ttl_seconds
        self.cooldown_seconds = cooldown_seconds
        self.pepper = pepper
        self.digest_prefix = digest_prefix

    async def reserve(self, scene: str, target: str) -> None:
        ok = await self.redis.set(
            self._cooldown_key(scene, target), "1", nx=True, ex=self.cooldown_seconds
        )
        if not ok:
            raise CodeRateLimitError()

    async def save(self, scene: str, target: str, code: str) -> None:
        digest = code_digest(target, code, self.pepper, prefix=self.digest_prefix)
        await self.redis.set(self._code_key(scene, target), digest, ex=self.ttl_seconds)
        await self.redis.delete(self._attempt_key(scene, target))

    async def rollback(self, scene: str, target: str) -> None:
        """发信/发短信失败时释放冷却与已写入的摘要，让用户可以立刻重试。"""
        await self.redis.delete(self._cooldown_key(scene, target), self._code_key(scene, target))

    async def verify(self, scene: str, target: str, code: str) -> None:
        stored = await self.redis.get(self._code_key(scene, target))
        if not stored:
            raise AuthFailedError("验证码错误或已失效")
        attempts = int(await self.redis.incr(self._attempt_key(scene, target)))
        await self.redis.expire(self._attempt_key(scene, target), self.ttl_seconds)
        if attempts > _MAX_ATTEMPTS:
            await self.redis.delete(self._code_key(scene, target), self._attempt_key(scene, target))
            raise AuthFailedError("验证码错误次数过多，请重新获取")
        expected = code_digest(target, code, self.pepper, prefix=self.digest_prefix)
        if not hmac.compare_digest(str(stored), expected):
            raise AuthFailedError("验证码错误或已失效")
        # 校验通过立即删除：一次性使用，防重放。
        await self.redis.delete(self._code_key(scene, target), self._attempt_key(scene, target))

    def _code_key(self, scene: str, target: str) -> str:
        return f"{self.namespace}:code:{scene}:{target}"

    def _cooldown_key(self, scene: str, target: str) -> str:
        return f"{self.namespace}:cooldown:{scene}:{target}"

    def _attempt_key(self, scene: str, target: str) -> str:
        return f"{self.namespace}:attempts:{scene}:{target}"
