"""密码使用 Argon2id。刷新凭证只保存 SHA-256。"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type

_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("密码过短")
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def generate_refresh_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw_token: str) -> str:
    if len(raw_token) < 16:
        raise ValueError("刷新凭证过短")
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def verify_refresh_token(token_hash: str, raw_token: str) -> bool:
    try:
        actual = hash_refresh_token(raw_token)
    except ValueError:
        return False
    return hmac.compare_digest(token_hash, actual)
