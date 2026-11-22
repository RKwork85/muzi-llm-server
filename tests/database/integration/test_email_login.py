"""邮箱验证码注册的端到端校验。需要本机 PostgreSQL + Redis，SMTP 用假实现替代。"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.core.database.redis import close_redis, get_redis
from app.core.database.session import dispose_engine, get_session_factory, init_engine
from app.main import create_app
from app.services.auth_service import AuthService
from tests.database.integration.test_postgres import _identity


@pytest.fixture
async def ready_database():
    get_settings.cache_clear()
    loaded = Settings()
    init_engine(loaded)
    try:
        yield loaded
    finally:
        await close_redis()
        await dispose_engine()


async def _delete_user(user_id: uuid.UUID) -> None:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        await session.commit()


async def _clear_email_keys(email: str) -> None:
    redis = get_redis()
    keys = [
        f"email:code:register:{email}",
        f"email:cooldown:register:{email}",
        f"email:attempts:register:{email}",
        f"email:limit:addr:{email}",
    ]
    keys = [key for key in keys if await redis.exists(key)]
    if keys:
        await redis.delete(*keys)


async def test_email_code_registration_flow(ready_database: Settings, monkeypatch) -> None:
    del ready_database
    email, username = _identity()
    password = "email-code-pass-1"
    captured: dict[str, str] = {}

    def fake_send(_settings, to_email: str, code: str, scene: str) -> None:
        captured.update(email=to_email, code=code, scene=scene)

    monkeypatch.setattr(
        "app.services.auth_service.get_settings",
        lambda: Settings(
            mail_enabled=True,
            mail_host="smtp.qq.com",
            mail_username="noreply@qq.com",
            mail_from="noreply@qq.com",
            mail_password="unit-test-pepper",
        ),
    )
    monkeypatch.setattr("app.services.auth_service.send_mail_code", fake_send)

    app = create_app()
    user_id: uuid.UUID | None = None
    try:
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # 未注册邮箱：注册场景可以收码。
                sent = await client.post(
                    "/api/v1/auth/email/send-code",
                    json={"email": email, "scene": "register"},
                )
                assert sent.status_code == 200, sent.text
                assert sent.json()["expires_in"] == 600
                assert captured["email"] == email
                assert captured["scene"] == "register"
                code = captured["code"]

                # 冷却期内重复发送必须被拒。
                again = await client.post(
                    "/api/v1/auth/email/send-code",
                    json={"email": email, "scene": "register"},
                )
                assert again.status_code == 429, again.text

                # 验证码错误不能建号。
                wrong = "000000" if code != "000000" else "111111"
                rejected = await client.post(
                    "/api/v1/auth/register",
                    json={
                        "email": email,
                        "username": username,
                        "password": password,
                        "code": wrong,
                    },
                )
                assert rejected.status_code == 401, rejected.text

                created = await client.post(
                    "/api/v1/auth/register",
                    json={
                        "email": email,
                        "username": username,
                        "password": password,
                        "code": code,
                    },
                )
                assert created.status_code == 201, created.text
                body = created.json()
                user_id = uuid.UUID(body["user"]["id"])
                assert body["user"]["email"] == email
                assert body["token"]

                # 重复提交同一邮箱会被去重挡住（早于验证码校验，所以是 409 而不是 401）。
                reused = await client.post(
                    "/api/v1/auth/register",
                    json={
                        "email": email,
                        "username": f"{username}b",
                        "password": password,
                        "code": code,
                    },
                )
                assert reused.status_code == 409, reused.text
                assert reused.json()["error"]["code"] == "ACCOUNT_EXISTS"

                # 已注册邮箱不再需要验证码，直接被拒并引导去登录。
                existing = await client.post(
                    "/api/v1/auth/email/send-code",
                    json={"email": email, "scene": "register"},
                )
                assert existing.status_code == 409, existing.text
                assert existing.json()["error"]["code"] == "EMAIL_ALREADY_REGISTERED"

                # 注册后可以用密码登录。
                logged = await client.post(
                    "/api/v1/auth/login",
                    json={"email": email, "password": password},
                )
                assert logged.status_code == 200, logged.text

            async with get_session_factory()() as session:
                verified = await session.scalar(
                    text("SELECT email_verified_at FROM users WHERE id = :id"),
                    {"id": user_id},
                )
                assert verified is not None

            # Redis / 连接池要趁 lifespan 还没关闭时清理。
            await _clear_email_keys(email)
            await _delete_user(user_id)
            user_id = None
    finally:
        if user_id is not None:
            init_engine(Settings())
            await _delete_user(user_id)
            await dispose_engine()
