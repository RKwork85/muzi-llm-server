"""邮箱密码注册和登录。接口层不写查询。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AccountDisabledError,
    AccountExistsError,
    AuthFailedError,
    InvalidRequestError,
)
from app.core.security import generate_refresh_token, hash_password, hash_refresh_token, verify_password
from app.models.mixins import utcnow
from app.models.tables import RefreshToken, User, UserCredential

_USERNAME = re.compile(r"^[a-z][a-z0-9_]{2,31}$")
_TOKEN_TTL = timedelta(days=30)
_DUMMY_HASH: str | None = None


@dataclass
class IssuedLogin:
    user: User
    token: str
    expires_at: datetime


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    if len(email) > 320 or " " in email or "@" not in email or email.find("@") < 1:
        raise InvalidRequestError("邮箱格式不正确")
    if email.endswith("@") or email.startswith("@"):
        raise InvalidRequestError("邮箱格式不正确")
    return email


def normalize_username(value: str) -> str:
    username = value.strip().lower()
    if _USERNAME.fullmatch(username) is None:
        raise InvalidRequestError("用户名需为 3 到 32 位，以小写字母开头，只能包含小写字母、数字和下划线")
    return username


def normalize_password(value: str) -> str:
    if len(value) < 8 or len(value) > 128:
        raise InvalidRequestError("密码长度需为 8 到 128 位")
    return value


def normalize_display_name(value: str | None) -> str | None:
    if value is None:
        return None
    name = value.strip()
    if not name:
        return None
    if len(name) > 120:
        raise InvalidRequestError("显示名过长")
    return name


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("not-a-real-password")
    return _DUMMY_HASH


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def register(
        self,
        email: str,
        username: str,
        password: str,
        display_name: str | None = None,
    ) -> IssuedLogin:
        email = normalize_email(email)
        username = normalize_username(username)
        password = normalize_password(password)
        display_name = normalize_display_name(display_name)
        if await self._email_taken(email):
            raise AccountExistsError("邮箱已被使用")
        if await self._username_taken(username):
            raise AccountExistsError("用户名已被使用")
        user = User(email=email, username=username, display_name=display_name, status="active")
        self.session.add(user)
        try:
            await self.session.flush()
            self.session.add(
                UserCredential(
                    user_id=user.id,
                    provider="email_password",
                    provider_uid=email,
                    password_hash=hash_password(password),
                )
            )
            issued = await self._issue(user)
            await self.session.flush()
        except IntegrityError:
            raise AccountExistsError("邮箱或用户名已被使用") from None
        return issued

    async def login(self, email: str, password: str) -> IssuedLogin:
        try:
            email = normalize_email(email)
        except InvalidRequestError:
            raise AuthFailedError() from None
        user = await self.session.scalar(select(User).where(User.email == email))
        credential = None
        if user is not None:
            credential = await self.session.scalar(
                select(UserCredential).where(
                    UserCredential.user_id == user.id,
                    UserCredential.provider == "email_password",
                )
            )
        password_hash = credential.password_hash if credential and credential.password_hash else _dummy_hash()
        matched = verify_password(password_hash, password)
        if user is None or credential is None or not credential.password_hash or not matched:
            raise AuthFailedError()
        if user.status != "active":
            raise AccountDisabledError()
        issued = await self._issue(user)
        await self.session.flush()
        return issued

    async def user_for_token(self, raw_token: str) -> User | None:
        if len(raw_token) < 16:
            return None
        digest = hash_refresh_token(raw_token)
        row = await self.session.scalar(
            select(RefreshToken).where(
                RefreshToken.token_hash == digest,
                RefreshToken.revoked_at.is_(None),
            )
        )
        if row is None or row.expires_at <= utcnow():
            return None
        user = await self.session.get(User, row.user_id)
        if user is None or user.status != "active":
            return None
        return user

    async def _email_taken(self, email: str) -> bool:
        found = await self.session.scalar(select(User.id).where(User.email == email))
        return found is not None

    async def _username_taken(self, username: str) -> bool:
        found = await self.session.scalar(select(User.id).where(User.username == username))
        return found is not None

    async def _issue(self, user: User) -> IssuedLogin:
        raw, token_hash = generate_refresh_token()
        expires_at = utcnow() + _TOKEN_TTL
        self.session.add(
            RefreshToken(
                user_id=user.id,
                token_hash=token_hash,
                expires_at=expires_at,
            )
        )
        return IssuedLogin(user=user, token=raw, expires_at=expires_at)
