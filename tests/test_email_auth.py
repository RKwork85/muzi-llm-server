"""邮箱验证码的存储、发送门槛和登录校验。不连接 SMTP，也不连接数据库。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.exceptions import (
    AuthFailedError,
    CodeRateLimitError,
    EmailNotRegisteredError,
    InvalidRequestError,
    MailDeliveryError,
)
from app.mail.sender import mail_configured
from app.mail.templates import build_code_message
from app.models.tables import RefreshToken
from app.services.auth_service import AuthService
from app.verification.codes import VerificationCodeStore, code_digest


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, *keys):
        removed = 0
        for key in keys:
            if self.store.pop(key, None) is not None:
                removed += 1
        return removed

    async def incr(self, key):
        value = int(self.store.get(key) or 0) + 1
        self.store[key] = str(value)
        return value

    async def decr(self, key):
        value = int(self.store.get(key) or 0) - 1
        self.store[key] = str(value)
        return value

    async def expire(self, key, ttl):
        return True


class FakeSession:
    """只实现 AuthService 会用到的方法：查用户、写刷新凭证、flush。"""

    def __init__(self, user=None) -> None:
        self.user = user
        self.added: list[object] = []

    async def scalar(self, _stmt):
        return self.user

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None


def _settings(**overrides) -> SimpleNamespace:
    data = {
        "mail_enabled": True,
        "mail_host": "smtp.qq.com",
        "mail_port": 465,
        "mail_username": "tester@qq.com",
        "mail_password": "authcode",
        "mail_from": "tester@qq.com",
        "mail_from_name": "MUZI WORLD",
        "mail_use_ssl": True,
        "mail_start_tls": False,
        "mail_timeout_seconds": 10,
        "mail_code_ttl_seconds": 600,
        "mail_cooldown_seconds": 60,
        "mail_code_length": 6,
        "mail_hourly_limit_per_email": 5,
        "mail_hourly_limit_per_ip": 20,
        "mail_daily_limit": 200,
        "mail_blocked_domains": "phone.local",
        "mail_subject": "MUZI WORLD 登录验证码",
        "mail_code_pepper": "",
        "mail_blocked_domain_set": lambda: {"phone.local"},
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _patch(monkeypatch, redis, settings, sender) -> None:
    monkeypatch.setattr("app.services.auth_service.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.auth_service.get_redis_or_none", lambda: redis)
    monkeypatch.setattr("app.services.auth_service.send_mail_code", sender)


def test_mail_configured_requires_host_and_from() -> None:
    assert mail_configured(_settings()) is True
    assert mail_configured(_settings(mail_enabled=False)) is False
    assert mail_configured(_settings(mail_from="")) is False


def test_code_message_contains_code_ttl_and_both_parts() -> None:
    message = build_code_message(_settings(), "user@qq.com", "654321", scene="login")
    body = message.get_body(preferencelist=("plain",)).get_content()
    assert "654321" in body
    assert "10 分钟" in body
    assert "tester@qq.com" in str(message["From"])
    assert message.get_content_type() == "multipart/alternative"
    # 纯 HTML 更容易被判垃圾邮件，必须同时带 text/plain。
    assert message.get_body(preferencelist=("html",)).get_content().count("654321") == 1


def test_email_digest_is_channel_scoped() -> None:
    """同一个 target + code，邮箱摘要必须与短信摘要不同，避免互相顶替。"""
    sms = code_digest("13800138000", "123456", "pepper")
    email = code_digest("13800138000", "123456", "pepper", prefix="email:")
    assert sms != email


@pytest.mark.asyncio
async def test_send_email_code_keeps_digest_and_cooldown(monkeypatch) -> None:
    redis = FakeRedis()
    sent: dict[str, str] = {}

    def fake_send(_settings_obj, email: str, code: str, scene: str) -> None:
        sent.update(email=email, code=code, scene=scene)

    _patch(monkeypatch, redis, _settings(), fake_send)
    session = FakeSession(SimpleNamespace(id="u1"))

    result = await AuthService(session).send_email_code("User@QQ.com", "login", client_ip="1.1.1.1")

    assert result["cooldown_seconds"] == 60
    assert result["expires_in"] == 600
    assert sent["email"] == "user@qq.com"
    assert sent["scene"] == "login"
    key = "email:code:login:user@qq.com"
    # pepper 回落到 MAIL_PASSWORD，摘要按 email: 前缀计算。
    assert redis.store[key] == code_digest("user@qq.com", sent["code"], "authcode", prefix="email:")
    assert sent["code"] not in redis.store.values()

    with pytest.raises(CodeRateLimitError):
        await AuthService(session).send_email_code("user@qq.com", "login")


@pytest.mark.asyncio
async def test_send_email_code_rejects_unregistered_email(monkeypatch) -> None:
    redis = FakeRedis()
    sent: list[str] = []
    _patch(monkeypatch, redis, _settings(), lambda *_args: sent.append("sent"))

    with pytest.raises(EmailNotRegisteredError):
        await AuthService(FakeSession(None)).send_email_code("ghost@qq.com", "login")

    assert sent == []
    assert not any(key.startswith("email:code") for key in redis.store)


@pytest.mark.asyncio
async def test_send_email_code_rejects_blocked_domain(monkeypatch) -> None:
    redis = FakeRedis()
    _patch(monkeypatch, redis, _settings(), lambda *_args: None)

    with pytest.raises(InvalidRequestError):
        await AuthService(FakeSession(None)).send_email_code("13800138000@phone.local", "login")


@pytest.mark.asyncio
async def test_send_email_code_rolls_back_when_delivery_fails(monkeypatch) -> None:
    redis = FakeRedis()

    def boom(*_args) -> None:
        raise MailDeliveryError()

    _patch(monkeypatch, redis, _settings(), boom)

    with pytest.raises(MailDeliveryError):
        await AuthService(FakeSession(SimpleNamespace(id="u1"))).send_email_code(
            "user@qq.com", "login"
        )

    # 冷却必须释放，否则用户要空等 60 秒；额度也要退回。
    assert "email:cooldown:login:user@qq.com" not in redis.store
    assert redis.store["email:limit:addr:user@qq.com"] == "0"
    assert redis.store["email:limit:global"] == "0"


@pytest.mark.asyncio
async def test_login_rejects_wrong_code(monkeypatch) -> None:
    redis = FakeRedis()
    settings = _settings()
    sent: dict[str, str] = {}
    _patch(monkeypatch, redis, settings, lambda _s, _e, code, _sc: sent.update(code=code))
    user = SimpleNamespace(id="u1", status="active", email="user@qq.com", email_verified_at=None)
    session = FakeSession(user)

    await AuthService(session).send_email_code("user@qq.com", "login")
    wrong = "000000" if sent["code"] != "000000" else "111111"

    with pytest.raises(AuthFailedError):
        await AuthService(session).login_with_email("user@qq.com", wrong)


@pytest.mark.asyncio
async def test_login_rejects_unregistered_email(monkeypatch) -> None:
    redis = FakeRedis()
    settings = _settings()
    _patch(monkeypatch, redis, settings, lambda *_args: None)
    redis.store["email:code:login:ghost@qq.com"] = code_digest(
        "ghost@qq.com", "123456", "authcode", prefix="email:"
    )

    with pytest.raises(EmailNotRegisteredError):
        await AuthService(FakeSession(None)).login_with_email("ghost@qq.com", "123456")


@pytest.mark.asyncio
async def test_login_succeeds_and_marks_email_verified(monkeypatch) -> None:
    redis = FakeRedis()
    settings = _settings()
    _patch(monkeypatch, redis, settings, lambda *_args: None)
    user = SimpleNamespace(id="u1", status="active", email="user@qq.com", email_verified_at=None)
    session = FakeSession(user)
    redis.store["email:code:login:user@qq.com"] = code_digest(
        "user@qq.com", "123456", "authcode", prefix="email:"
    )

    issued = await AuthService(session).login_with_email("user@qq.com", "123456")

    assert issued.token
    assert user.email_verified_at is not None
    assert any(isinstance(item, RefreshToken) for item in session.added)
    # 一次性使用：验证通过后摘要被删除。
    assert "email:code:login:user@qq.com" not in redis.store


@pytest.mark.asyncio
async def test_verify_destroys_code_after_too_many_attempts() -> None:
    redis = FakeRedis()
    store = VerificationCodeStore(
        redis,
        namespace="email",
        ttl_seconds=600,
        cooldown_seconds=60,
        pepper="pepper",
        digest_prefix="email:",
    )
    await store.save("login", "user@qq.com", "123456")

    for _ in range(6):
        with pytest.raises(AuthFailedError):
            await store.verify("login", "user@qq.com", "000000")

    assert "email:code:login:user@qq.com" not in redis.store
