"""手机验证码的存储、发送门槛和登录校验。不连接阿里云。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from app.core.exceptions import AuthFailedError, FeatureNotReadyError, SmsRateLimitError
from app.services.auth_service import AuthService, phone_account_fields
from app.sms.codes import code_digest
from app.sms.receipts import token_needs_refresh
from app.sms.sender import sms_configured


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

    async def expire(self, key, ttl):
        return True


def _settings(**overrides):
    data = {
        "sms_access_key_id": "test-key",
        "sms_access_key_secret": "test-secret",
        "sms_sign_name": "求索",
        "sms_template_code": "SMS_000000",
        "sms_template_param_key": "code",
        "sms_endpoint": "dysmsapi.aliyuncs.com",
        "sms_code_ttl_seconds": 300,
        "sms_cooldown_seconds": 60,
        "sms_code_length": 6,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_phone_account_fields_hide_full_number() -> None:
    email, username, display_name = phone_account_fields("13800138000")
    assert email == "13800138000@phone.local"
    assert username == "u13800138000"
    assert display_name == "138****8000"


def test_sms_configured_requires_sign_and_template() -> None:
    assert sms_configured(_settings()) is True
    assert sms_configured(_settings(sms_template_code="")) is False


def test_token_refresh_window() -> None:
    assert token_needs_refresh(None, datetime(2026, 11, 12, 12, 0, 0)) is True
    assert token_needs_refresh("2026-11-12 12:01:30", datetime(2026, 11, 12, 12, 0, 0)) is True
    assert token_needs_refresh("2026-11-12 12:10:00", datetime(2026, 11, 12, 12, 0, 0)) is False


@pytest.mark.asyncio
async def test_send_phone_code_keeps_digest_and_cooldown(monkeypatch) -> None:
    redis = FakeRedis()
    sent: dict[str, str] = {}
    settings = _settings()

    def fake_send(_settings_obj, phone: str, code: str) -> None:
        sent["phone"] = phone
        sent["code"] = code

    monkeypatch.setattr("app.services.auth_service.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.auth_service.get_redis_or_none", lambda: redis)
    monkeypatch.setattr("app.services.auth_service.send_verification_code", fake_send)

    result = await AuthService(None).send_phone_code("138-0013-8000", "login")
    assert result["cooldown_seconds"] == 60
    assert sent["phone"] == "13800138000"
    digest = code_digest("13800138000", sent["code"], "test-secret")
    assert redis.store["sms:code:login:13800138000"] == digest
    assert sent["code"] not in redis.store.values()

    with pytest.raises(SmsRateLimitError):
        await AuthService(None).send_phone_code("13800138000", "login")


@pytest.mark.asyncio
async def test_login_rejects_wrong_code_without_database(monkeypatch) -> None:
    redis = FakeRedis()
    settings = _settings()
    monkeypatch.setattr("app.services.auth_service.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.auth_service.get_redis_or_none", lambda: redis)
    monkeypatch.setattr("app.services.auth_service.send_verification_code", lambda *_args: None)
    await AuthService(None).send_phone_code("13800138000", "login")
    redis.store.pop("sms:cooldown:login:13800138000", None)

    with pytest.raises(AuthFailedError):
        await AuthService(None).login_with_phone("13800138000", "000000")


@pytest.mark.asyncio
async def test_send_phone_code_requires_configuration(monkeypatch) -> None:
    monkeypatch.setattr("app.services.auth_service.get_settings", lambda: _settings(sms_sign_name=""))
    with pytest.raises(FeatureNotReadyError):
        await AuthService(None).send_phone_code("13800138000", "login")
