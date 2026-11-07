"""邮箱注册和登录。只在本机数据库上运行。"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.main import create_app
from app.core.database.redis import close_redis
from app.core.database.session import dispose_engine, get_session_factory, init_engine
from app.core.exceptions import AccountExistsError, AuthFailedError
from app.core.security import hash_refresh_token
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


async def test_register_login_and_reject_duplicates(ready_database: Settings) -> None:
    del ready_database
    email, username = _identity()
    password = "register-pass-1"
    user_id: uuid.UUID | None = None
    factory = get_session_factory()
    try:
        async with factory() as session:
            issued = await AuthService(session).register(email, username, password, "  ")
            await session.commit()
            user_id = issued.user.id
            assert issued.user.email == email
            assert issued.user.display_name is None
            assert issued.user.status == "active"
            stored = await session.scalar(
                text("SELECT token_hash FROM refresh_tokens WHERE user_id = :id"),
                {"id": user_id},
            )
            assert stored == hash_refresh_token(issued.token)
            assert issued.token not in str(stored)

        async with factory() as session:
            logged = await AuthService(session).login(email.upper(), password)
            await session.commit()
            assert logged.user.id == user_id
            assert logged.token != ""

        async with factory() as session:
            with pytest.raises(AuthFailedError):
                await AuthService(session).login(email, "wrong-pass-1")
            await session.rollback()

        async with factory() as session:
            with pytest.raises(AccountExistsError, match="邮箱"):
                await AuthService(session).register(email, f"n{username[1:]}", password, None)
            await session.rollback()

        async with factory() as session:
            with pytest.raises(AccountExistsError, match="用户名"):
                await AuthService(session).register(f"other-{email}", username, password, None)
            await session.rollback()
    finally:
        if user_id is not None:
            await _delete_user(user_id)


async def test_register_and_login_http() -> None:
    email, username = _identity()
    password = "route-pass-1"
    app = create_app()
    user_id: uuid.UUID | None = None
    try:
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                created = await client.post(
                    "/api/v1/auth/register",
                    json={"email": email, "username": username, "password": password},
                )
                assert created.status_code == 201, created.text
                body = created.json()
                user_id = uuid.UUID(body["user"]["id"])
                assert body["user"]["email"] == email
                assert body["token_type"] == "bearer"
                assert "password" not in body["user"]

                logged = await client.post(
                    "/api/v1/auth/login",
                    json={"email": email, "password": password},
                )
                assert logged.status_code == 200, logged.text
                assert logged.json()["user"]["username"] == username

                rejected = await client.post(
                    "/api/v1/auth/login",
                    json={"email": email, "password": "wrong-pass-1"},
                )
                assert rejected.status_code == 401
                assert rejected.json()["error"]["message"] == "邮箱或密码错误"
            if user_id is not None:
                await _delete_user(user_id)
                user_id = None
    finally:
        if user_id is not None:
            init_engine(Settings())
            await _delete_user(user_id)
            await dispose_engine()
