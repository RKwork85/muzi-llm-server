"""邮箱密码注册和登录。接口层不写查询。"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database.redis import get_redis_or_none
from app.core.exceptions import (
    AccountDisabledError,
    AccountExistsError,
    AuthFailedError,
    DatabaseUnavailableError,
    FeatureNotReadyError,
    InvalidRequestError,
)
from app.sms.codes import SmsCodeStore, new_code
from app.sms.sender import send_verification_code, sms_configured
from app.core.security import generate_refresh_token, hash_password, hash_refresh_token, verify_password
from app.authorization.domain.catalog import MEMBER_ROLE_CODE
from app.models.mixins import utcnow
from app.models.rbac import Role, UserRole
from app.models.tables import RefreshToken, User, UserCredential

_USERNAME = re.compile(r"^[a-z][a-z0-9_]{2,31}$")
_PHONE = re.compile(r"^1[3-9]\d{9}$")
_SMS_CODE = re.compile(r"^\d{4,8}$")
_TOKEN_TTL = timedelta(days=30)
_DUMMY_HASH: str | None = None
_PHONE_EMAIL_DOMAIN = "phone.local"


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


def normalize_phone(value: str) -> str:
    """归一化中国大陆手机号：去空格与 +86 前缀后校验 11 位。"""
    phone = value.strip().replace(" ", "").replace("-", "")
    if phone.startswith("+86"):
        phone = phone[3:]
    elif phone.startswith("86") and len(phone) == 13:
        phone = phone[2:]
    if _PHONE.fullmatch(phone) is None:
        raise InvalidRequestError("请输入有效的中国大陆手机号")
    return phone


def normalize_sms_code(value: str) -> str:
    code = value.strip()
    if _SMS_CODE.fullmatch(code) is None:
        raise InvalidRequestError("验证码格式不正确")
    return code


def phone_account_fields(phone: str) -> tuple[str, str, str]:
    """手机号账户的内部邮箱、用户名和显示名。邮箱仅占位，不对外展示。"""
    masked = f"{phone[:3]}****{phone[-4:]}"
    return f"{phone}@{_PHONE_EMAIL_DOMAIN}", f"u{phone}", masked


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
            await self._assign_default_role(user.id)
            issued = await self._issue(user)
            await self.session.flush()
            await self._grant_default_quota(user.id)
        except IntegrityError:
            raise AccountExistsError("邮箱或用户名已被使用") from None
        return issued

    async def _assign_default_role(self, user_id: object) -> None:
        """给新注册用户赋予默认 member 角色。角色缺失或表未就绪时静默跳过。"""
        try:
            role = await self.session.scalar(select(Role).where(Role.code == MEMBER_ROLE_CODE))
        except Exception:
            return
        if role is not None:
            self.session.add(UserRole(user_id=user_id, role_id=role.id))

    async def _grant_default_quota(self, user_id: object) -> None:
        """为新用户或缺失默认额度的老用户补齐赠送额度（幂等）。"""
        from app.authorization.application.audit_service import QuotaService

        try:
            async with self.session.begin_nested():
                await QuotaService(self.session).ensure_default_quota(user_id)
        except Exception:
            # 保存点回滚额度写入，登录事务本身仍可提交。
            return

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
        await self._grant_default_quota(user.id)
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

    async def reset_password(self, user_id: object, new_password: str) -> None:
        """管理员重置用户密码：只写入新密码哈希，并吊销该用户全部登录凭证。

        不回显旧密码；旧密码本就不可逆，无法也无法被读取。
        """
        new_password = normalize_password(new_password)
        credential = await self.session.scalar(
            select(UserCredential).where(
                UserCredential.user_id == user_id,
                UserCredential.provider == "email_password",
            )
        )
        if credential is None:
            raise InvalidRequestError("该用户没有邮箱密码凭证")
        credential.password_hash = hash_password(new_password)
        # 重置后旧会话全部失效，需重新登录。
        await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )

    async def change_password(self, user_id: object, old_password: str, new_password: str) -> None:
        """用户自助修改密码：校验旧密码后写入新哈希，并吊销该用户全部登录凭证。

        修改成功后所有设备需重新登录。
        """
        credential = await self.session.scalar(
            select(UserCredential).where(
                UserCredential.user_id == user_id,
                UserCredential.provider == "email_password",
            )
        )
        if credential is None or not credential.password_hash:
            raise InvalidRequestError("该账户未使用邮箱密码登录，无法修改密码")
        if not verify_password(credential.password_hash, old_password):
            raise AuthFailedError("当前密码不正确")
        new_password = normalize_password(new_password)
        credential.password_hash = hash_password(new_password)
        await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )

    async def revoke_token(self, raw_token: str) -> None:
        """吊销指定登录凭证（退出登录）。幂等，token 无效时不报错。"""
        if len(raw_token) < 16:
            return
        digest = hash_refresh_token(raw_token)
        await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == digest, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )

    def _sms_store(self) -> SmsCodeStore:
        settings = get_settings()
        if not sms_configured(settings):
            raise FeatureNotReadyError("短信服务尚未配置")
        redis = get_redis_or_none()
        if redis is None:
            raise DatabaseUnavailableError("验证码服务暂不可用")
        return SmsCodeStore(
            redis,
            ttl_seconds=settings.sms_code_ttl_seconds,
            cooldown_seconds=settings.sms_cooldown_seconds,
            pepper=settings.sms_access_key_secret,
        )

    async def send_phone_code(self, phone: str, scene: str = "login") -> dict[str, object]:
        """生成验证码、写入 Redis，并调用阿里云短信下发。"""
        phone = normalize_phone(phone)
        if scene not in {"login", "bind"}:
            raise InvalidRequestError("不支持的验证码场景")
        settings = get_settings()
        store = self._sms_store()
        await store.reserve(scene, phone)
        code = new_code(settings.sms_code_length)
        try:
            await store.save(scene, phone, code)
            await asyncio.to_thread(send_verification_code, settings, phone, code)
        except Exception:
            await store.rollback(scene, phone)
            raise
        return {
            "ok": True,
            "cooldown_seconds": settings.sms_cooldown_seconds,
            "message": "验证码已发送",
        }

    async def login_with_phone(self, phone: str, code: str) -> IssuedLogin:
        """校验验证码后登录；手机号尚未注册时自动创建账户。"""
        phone = normalize_phone(phone)
        code = normalize_sms_code(code)
        await self._sms_store().verify("login", phone, code)
        user = await self.session.scalar(select(User).where(User.phone == phone))
        if user is None:
            user = await self._register_phone_user(phone)
        elif user.status != "active":
            raise AccountDisabledError()
        issued = await self._issue(user)
        await self.session.flush()
        await self._grant_default_quota(user.id)
        return issued

    async def _register_phone_user(self, phone: str) -> User:
        email, username, display_name = phone_account_fields(phone)
        if await self._email_taken(email) or await self._username_taken(username):
            raise AccountExistsError("该手机号已被使用")
        user = User(
            email=email,
            username=username,
            display_name=display_name,
            phone=phone,
            status="active",
        )
        self.session.add(user)
        try:
            await self.session.flush()
            self.session.add(
                UserCredential(
                    user_id=user.id,
                    provider="phone",
                    provider_uid=phone,
                )
            )
            await self._assign_default_role(user.id)
            await self.session.flush()
        except IntegrityError:
            raise AccountExistsError("该手机号已被使用") from None
        return user

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
