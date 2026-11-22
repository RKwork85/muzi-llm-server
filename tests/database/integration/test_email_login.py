"""邮箱验证码登录的端到端校验。需要本机 PostgreSQL + Redis，SMTP 用假实现替代。"""

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
    keys = [f"email:code:login:{email}", f"email:cooldown:login:{email}",
            f"email:attempts:login:{email}", f"email:limit:addr:{email}"]
    keys = [key for key in keys if await redis.exists(key)]
    if keys:
        await redis.delete(*keys)


async def test_email_code_login_flow(ready_database: Settings, monkeypatch) -> None:
    del ready_database
    email, username = _identity()
    password = "email-code-pass-1"
    captured: dict[str, str] = {}

    def fake_send(_settings, to_email: str, code: str, scene: str) -> None:
        captured.update(email=to_email, code=code, scene=scene)

    # 用真实 Settings 覆盖，确保邮件字段齐全；发送被替换为纯内存实现。
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
            factory = get_session_factory()
            async with factory() as session:
                issued = await AuthService(session).register(email, username, password, None)
                await session.commit()
                user_id = issued.user.id

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # 未注册邮箱：404，前端据此引导去注册。
                missing = await client.post(
                    "/api/v1/auth/email/send-code",
                    json={"email": f"nobody-{uuid.uuid4().hex[:8]}@example.com"},
                )
                assert missing.status_code == 404, missing.text
                assert missing.json()["error"]["code"] == "EMAIL_NOT_REGISTERED"

                sent = await client.post(
                    "/api/v1/auth/email/send-code",
                    json={"email": email, "scene": "login"},
                )
                assert sent.status_code == 200, sent.text
                assert sent.json()["expires_in"] == 600
                assert captured["email"] == email
                code = captured["code"]

                # 冷却期内重复发送必须被拒。
                again = await client.post(
                    "/api/v1/auth/email/send-code",
                    json={"email": email, "scene": "login"},
                )
                assert again.status_code == 429, again.text

                wrong = "000000" if code != "000000" else "111111"
                rejected = await client.post(
                    "/api/v1/auth/email/login",
                    json={"email": email, "code": wrong},
                )
                assert rejected.status_code == 401, rejected.text

                logged = await client.post(
                    "/api/v1/auth/email/login",
                    json={"email": email, "code": code},
                )
                assert logged.status_code == 200, logged.text
                body = logged.json()
                assert uuid.UUID(body["user"]["id"]) == user_id
                assert body["token"]

                # 验证码一次性：同一个码不能再次使用。
                reused = await client.post(
                    "/api/v1/auth/email/login",
                    json={"email": email, "code": code},
                )
                assert reused.status_code == 401, reused.text

            async with factory() as session:
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
