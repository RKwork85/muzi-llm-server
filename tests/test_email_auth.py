"""邮箱验证码的存储、发送门槛和登录校验。不连接 SMTP，也不连接数据库。"""

from __future__ import annotations

import email.header
import smtplib
from types import SimpleNamespace

import pytest

from app.core.exceptions import (
    AuthFailedError,
    CodeRateLimitError,
    EmailAlreadyRegisteredError,
    EmailNotRegisteredError,
    FeatureNotReadyError,
    InvalidRequestError,
    MailDeliveryError,
)
from app.mail.sender import mail_accounts, mail_configured, reset_health, send_verification_code
from app.mail.templates import build_code_message
from app.models.tables import RefreshToken, User
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
        "mail_from_name": "MUZI-让思想自由生长",
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
        "mail_subject": "MUZI-让思想自由生长 注册验证码",
        "mail_code_pepper": "",
        "mail_blocked_domain_set": lambda: {"phone.local"},
        # 备用账号默认不配置。
        "mail_backup_host": "",
        "mail_backup_port": 465,
        "mail_backup_username": "",
        "mail_backup_password": "",
        "mail_backup_from": "",
        "mail_backup_use_ssl": True,
        "mail_backup_start_tls": False,
        "mail_failover_cooldown_seconds": 120,
    }
    data.update(overrides)
    settings = SimpleNamespace(**data)
    # 真实 Settings 上的两个派生判断，SimpleNamespace 里补一份等价实现。
    settings.mail_primary_ready = lambda: bool(settings.mail_host and settings.mail_from)
    settings.mail_backup_ready = lambda: bool(
        settings.mail_backup_host and (settings.mail_backup_from or settings.mail_backup_username)
    )
    return settings


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
    assert "完成注册登录" in body
    assert "tester@qq.com" in str(message["From"])
    assert message.get_content_type() == "multipart/alternative"
    # 纯 HTML 更容易被判垃圾邮件，必须同时带 text/plain。
    assert message.get_body(preferencelist=("html",)).get_content().count("654321") == 1


def test_subject_says_registration_code() -> None:
    message = build_code_message(_settings(), "user@qq.com", "654321", scene="login")
    subject = str(email.header.make_header(email.header.decode_header(message["Subject"])))
    assert subject == "MUZI-让思想自由生长 注册验证码"


def test_body_does_not_repeat_brand_name() -> None:
    """品牌名只在标题出现一次：正文再写一遍会让收件箱列表看起来重复。"""
    message = build_code_message(_settings(), "user@qq.com", "654321", scene="login")
    body = message.get_body(preferencelist=("plain",)).get_content()
    html = message.get_body(preferencelist=("html",)).get_content()
    assert "MUZI" not in body
    assert "MUZI" not in html
    # 正文第一行直接给验证码，收件箱预览不会再把标题重复一遍。
    assert body.splitlines()[0] == "验证码：654321"


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
    # 注册场景：邮箱还不能存在。
    session = FakeSession(None)

    result = await AuthService(session).send_email_code("User@QQ.com", "register", client_ip="1.1.1.1")

    assert result["cooldown_seconds"] == 60
    assert result["expires_in"] == 600
    assert sent["email"] == "user@qq.com"
    assert sent["scene"] == "register"
    key = "email:code:register:user@qq.com"
    # pepper 回落到 MAIL_PASSWORD，摘要按 email: 前缀计算。
    assert redis.store[key] == code_digest("user@qq.com", sent["code"], "authcode", prefix="email:")
    assert sent["code"] not in redis.store.values()

    with pytest.raises(CodeRateLimitError):
        await AuthService(session).send_email_code("user@qq.com", "register")


@pytest.mark.asyncio
async def test_reset_password_rejects_unregistered_email(monkeypatch) -> None:
    redis = FakeRedis()
    _patch(monkeypatch, redis, _settings(), lambda *_args: None)
    redis.store["email:code:reset_password:ghost@qq.com"] = code_digest(
        "ghost@qq.com", "123456", "authcode", prefix="email:"
    )

    with pytest.raises(EmailNotRegisteredError):
        await AuthService(FakeSession(None)).reset_password("ghost@qq.com", "123456", "new-pass-1")


@pytest.mark.asyncio
async def test_reset_password_rejects_blocked_domain(monkeypatch) -> None:
    """手机号账号的占位邮箱不能被用来重置密码。"""
    redis = FakeRedis()
    _patch(monkeypatch, redis, _settings(), lambda *_args: None)

    with pytest.raises(InvalidRequestError):
        await AuthService(FakeSession(SimpleNamespace(id="u1"))).reset_password(
            "13800138000@phone.local", "123456", "new-pass-1"
        )


@pytest.mark.asyncio
async def test_reset_password_requires_mail_configured(monkeypatch) -> None:
    redis = FakeRedis()
    _patch(monkeypatch, redis, _settings(mail_enabled=False), lambda *_args: None)

    with pytest.raises(FeatureNotReadyError):
        await AuthService(FakeSession(SimpleNamespace(id="u1"))).reset_password(
            "user@qq.com", "123456", "new-pass-1"
        )


@pytest.mark.asyncio
async def test_reset_password_updates_hash_and_revokes_sessions(monkeypatch) -> None:
    redis = FakeRedis()
    _patch(monkeypatch, redis, _settings(), lambda *_args: None)
    credential = SimpleNamespace(password_hash="old-hash")
    executed: list[object] = []

    class Session:
        def __init__(self) -> None:
            self.calls = 0

        async def scalar(self, _stmt):
            self.calls += 1
            # 第一次查用户，第二次查 email_password 凭证。
            return user if self.calls == 1 else credential

        async def execute(self, stmt):
            executed.append(stmt)

    user = SimpleNamespace(id="u1", status="active", email="user@qq.com")
    redis.store["email:code:reset_password:user@qq.com"] = code_digest(
        "user@qq.com", "123456", "authcode", prefix="email:"
    )

    await AuthService(Session()).reset_password("user@qq.com", "123456", "new-pass-1")

    assert credential.password_hash != "old-hash"
    # 旧会话必须全部失效。
    assert len(executed) == 1
    assert "email:code:reset_password:user@qq.com" not in redis.store


@pytest.mark.asyncio
async def test_reset_password_rejects_wrong_code(monkeypatch) -> None:
    redis = FakeRedis()
    _patch(monkeypatch, redis, _settings(), lambda *_args: None)
    user = SimpleNamespace(id="u1", status="active", email="user@qq.com")
    redis.store["email:code:reset_password:user@qq.com"] = code_digest(
        "user@qq.com", "123456", "authcode", prefix="email:"
    )

    with pytest.raises(AuthFailedError):
        await AuthService(FakeSession(user)).reset_password("user@qq.com", "000000", "new-pass-1")


def test_reset_scene_has_its_own_subject_and_heading() -> None:
    """重置邮件不能沿用「注册验证码」的标题。"""
    message = build_code_message(_settings(), "user@qq.com", "123456", scene="reset_password")
    subject = str(email.header.make_header(email.header.decode_header(message["Subject"])))
    html = message.get_body(preferencelist=("html",)).get_content()
    assert "重置密码验证码" in subject
    assert "注册" not in subject
    assert "重置密码验证码" in html
    assert "完成重置密码" in html


@pytest.mark.asyncio
async def test_send_email_code_rejects_registered_email(monkeypatch) -> None:
    """已注册用户用密码登录，不需要验证码。"""
    redis = FakeRedis()
    sent: list[str] = []
    _patch(monkeypatch, redis, _settings(), lambda *_args: sent.append("sent"))

    with pytest.raises(EmailAlreadyRegisteredError):
        await AuthService(FakeSession(SimpleNamespace(id="u1"))).send_email_code(
            "user@qq.com", "register"
        )

    assert sent == []
    assert not any(key.startswith("email:code") for key in redis.store)


@pytest.mark.asyncio
async def test_send_email_code_bind_scene_requires_registered_email(monkeypatch) -> None:
    """bind / reset_password 是给已有账号用的，未注册邮箱不能收码。"""
    redis = FakeRedis()
    _patch(monkeypatch, redis, _settings(), lambda *_args: None)

    with pytest.raises(EmailNotRegisteredError):
        await AuthService(FakeSession(None)).send_email_code("ghost@qq.com", "reset_password")


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
        await AuthService(FakeSession(None)).send_email_code("user@qq.com", "register")

    # 冷却必须释放，否则用户要空等 60 秒；额度也要退回。
    assert "email:cooldown:register:user@qq.com" not in redis.store
    assert redis.store["email:limit:addr:user@qq.com"] == "0"
    assert redis.store["email:limit:global"] == "0"


@pytest.mark.asyncio
async def test_register_requires_code_when_mail_configured(monkeypatch) -> None:
    redis = FakeRedis()
    _patch(monkeypatch, redis, _settings(), lambda *_args: None)

    with pytest.raises(InvalidRequestError, match="验证码"):
        await AuthService(FakeSession(None)).register("new@qq.com", "newuser", "password-1")


@pytest.mark.asyncio
async def test_register_rejects_wrong_code(monkeypatch) -> None:
    redis = FakeRedis()
    _patch(monkeypatch, redis, _settings(), lambda *_args: None)
    redis.store["email:code:register:new@qq.com"] = code_digest(
        "new@qq.com", "123456", "authcode", prefix="email:"
    )

    with pytest.raises(AuthFailedError):
        await AuthService(FakeSession(None)).register(
            "new@qq.com", "newuser", "password-1", None, "000000"
        )


@pytest.mark.asyncio
async def test_register_with_valid_code_marks_email_verified(monkeypatch) -> None:
    redis = FakeRedis()
    _patch(monkeypatch, redis, _settings(), lambda *_args: None)
    redis.store["email:code:register:new@qq.com"] = code_digest(
        "new@qq.com", "123456", "authcode", prefix="email:"
    )
    session = FakeSession(None)

    issued = await AuthService(session).register(
        "new@qq.com", "newuser", "password-1", None, "123456"
    )

    assert issued.token
    created = [item for item in session.added if isinstance(item, User)]
    assert len(created) == 1
    assert created[0].email_verified_at is not None
    assert any(isinstance(item, RefreshToken) for item in session.added)
    # 一次性使用：注册成功后摘要被删除。
    assert "email:code:register:new@qq.com" not in redis.store


@pytest.mark.asyncio
async def test_register_refuses_when_mail_disabled(monkeypatch) -> None:
    """邮件没配置时不允许跳过验证，直接拒绝注册。"""
    redis = FakeRedis()
    _patch(monkeypatch, redis, _settings(mail_enabled=False), lambda *_args: None)

    with pytest.raises(FeatureNotReadyError):
        await AuthService(FakeSession(None)).register("new@qq.com", "newuser", "password-1")


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


# ---------------------------------------------------------------------------
# 发信账号池与故障切换
# ---------------------------------------------------------------------------

_BACKUP = {
    "mail_backup_host": "smtp.example.net",
    "mail_backup_username": "backup@example.net",
    "mail_backup_password": "backup-code",
    "mail_backup_from": "",
}


def _patch_smtp(monkeypatch, behaviour: dict[str, object]) -> list[str]:
    """把真实 SMTP 替换成按账号角色记录的假实现。

    behaviour[role] 可以是 Exception 实例（该账号失败）或 None（成功）。
    """
    used: list[str] = []

    def fake_send_with(account, message, timeout):
        used.append(account.role)
        outcome = behaviour.get(account.role)
        if isinstance(outcome, Exception):
            raise outcome
        return None

    monkeypatch.setattr("app.mail.sender._send_with", fake_send_with)
    return used


def test_accounts_are_ordered_primary_first() -> None:
    settings = _settings(**_BACKUP)
    roles = [account.role for account in mail_accounts(settings)]
    assert roles == ["primary", "backup"]
    # From 留空时回落到登录账号，否则 QQ 会以伪造发件人拒收。
    assert mail_accounts(settings)[1].from_addr == "backup@example.net"


def test_accounts_without_backup_only_has_primary() -> None:
    assert [a.role for a in mail_accounts(_settings())] == ["primary"]


def test_backup_alone_is_enough_to_enable_mail() -> None:
    settings = _settings(mail_host="", mail_from="", **_BACKUP)
    assert mail_configured(settings) is True
    assert [a.role for a in mail_accounts(settings)] == ["backup"]


def test_failover_to_backup_when_primary_is_down(monkeypatch) -> None:
    reset_health()
    used = _patch_smtp(
        monkeypatch,
        {"primary": ConnectionRefusedError(), "backup": None},
    )
    send_verification_code(_settings(**_BACKUP), "user@qq.com", "123456", "login")
    assert used == ["primary", "backup"]


def test_failing_account_is_skipped_during_cooldown(monkeypatch) -> None:
    reset_health()
    used = _patch_smtp(monkeypatch, {"primary": ConnectionRefusedError(), "backup": None})
    settings = _settings(**_BACKUP)

    send_verification_code(settings, "user@qq.com", "123456", "login")
    assert used == ["primary", "backup"]

    # 第二次发信不应该再撞一遍已知故障的主账号。
    used.clear()
    send_verification_code(settings, "user@qq.com", "123456", "login")
    assert used == ["backup"]


def test_cooldown_zero_always_retries_primary(monkeypatch) -> None:
    reset_health()
    used = _patch_smtp(monkeypatch, {"primary": ConnectionRefusedError(), "backup": None})
    settings = _settings(mail_failover_cooldown_seconds=0, **_BACKUP)

    send_verification_code(settings, "user@qq.com", "123456", "login")
    used.clear()
    send_verification_code(settings, "user@qq.com", "123456", "login")
    assert used == ["primary", "backup"]


def test_primary_recovery_takes_traffic_back(monkeypatch) -> None:
    reset_health()
    settings = _settings(**_BACKUP)
    _patch_smtp(monkeypatch, {"primary": ConnectionRefusedError(), "backup": None})
    send_verification_code(settings, "user@qq.com", "123456", "login")

    # 冷却结束后主账号重新参与，恢复成功后不再需要备用。
    used = _patch_smtp(monkeypatch, {"primary": None, "backup": None})
    monkeypatch.setattr("app.mail.sender.time.monotonic", lambda: 10**9)
    send_verification_code(settings, "user@qq.com", "123456", "login")
    assert used == ["primary"]


def test_all_accounts_down_raises_once(monkeypatch) -> None:
    reset_health()
    used = _patch_smtp(
        monkeypatch,
        {"primary": ConnectionRefusedError(), "backup": TimeoutError()},
    )
    with pytest.raises(MailDeliveryError):
        send_verification_code(_settings(**_BACKUP), "user@qq.com", "123456", "login")
    assert used == ["primary", "backup"]


def test_recipient_refused_does_not_try_backup(monkeypatch) -> None:
    reset_health()
    used = _patch_smtp(
        monkeypatch,
        {"primary": smtplib.SMTPRecipientsRefused({}), "backup": None},
    )
    # 收件人地址本身有问题，换发信账号也没用，不应浪费一次备用投递。
    with pytest.raises(MailDeliveryError):
        send_verification_code(_settings(**_BACKUP), "user@qq.com", "123456", "login")
    assert used == ["primary"]
