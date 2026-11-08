"""AES-256-GCM 凭证。主密钥来自环境，不写进源码或数据库。"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import Settings


class KeyConfigurationError(RuntimeError):
    pass


class KeyRing:
    """本地开发读 MCP_TOKEN_KEY。生产必须声明外部密钥来源。"""

    def __init__(self, settings: Settings) -> None:
        self._active = settings.mcp_key_id.strip() or "local"
        self._keys = {self._active: _material(settings.mcp_token_key)}
        for item in settings.mcp_previous_token_keys.split(","):
            if ":" not in item:
                continue
            key_id, secret = item.split(":", 1)
            key_id = key_id.strip()
            if key_id:
                self._keys[key_id] = _material(secret)

    @property
    def active_id(self) -> str:
        return self._active

    def encrypt(self, plain: str, *, user_id: str, connector_id: str, credential_type: str) -> str:
        if not self._active or self._active not in self._keys:
            raise KeyConfigurationError("缺少活动密钥")
        nonce = os.urandom(12)
        token = AESGCM(self._keys[self._active]).encrypt(nonce, plain.encode("utf-8"), _aad(user_id, connector_id, credential_type))
        return ".".join(
            [
                "v2",
                self._active,
                base64.urlsafe_b64encode(nonce).decode("ascii"),
                base64.urlsafe_b64encode(token).decode("ascii"),
            ]
        )

    def decrypt(self, sealed: str, *, user_id: str, connector_id: str, credential_type: str) -> str:
        version, key_id, nonce_text, body_text = _parts(sealed)
        if version != "v2":
            raise KeyConfigurationError("密文版本无效")
        key = self._keys.get(key_id)
        if key is None:
            raise KeyConfigurationError("未知密钥")
        nonce = base64.urlsafe_b64decode(nonce_text.encode("ascii"))
        body = base64.urlsafe_b64decode(body_text.encode("ascii"))
        try:
            plain = AESGCM(key).decrypt(nonce, body, _aad(user_id, connector_id, credential_type))
        except Exception as exc:
            raise KeyConfigurationError("密文无效") from exc
        return plain.decode("utf-8")


_DEV_KEY_IDS = frozenset({"", "local", "dev", "development", "test"})
_PLACEHOLDER_SECRETS = frozenset({"changeme", "secret", "password", "mcp_token_key", "test-token-key-012345"})


def validate_key_config(settings: Settings) -> None:
    """生产启动前拒绝缺失、过短、占位和开发用密钥。不记录密钥内容。"""
    if settings.runtime_env.strip().lower() != "production":
        return
    if settings.mcp_key_source.strip().lower() != "external":
        raise KeyConfigurationError("生产环境必须使用外部密钥来源")
    key_id = settings.mcp_key_id.strip()
    if key_id.lower() in _DEV_KEY_IDS:
        raise KeyConfigurationError("生产环境不能使用开发密钥编号")
    _require_secret(settings.mcp_token_key)
    for item in settings.mcp_previous_token_keys.split(","):
        if not item.strip():
            continue
        if ":" not in item:
            raise KeyConfigurationError("旧密钥格式无效")
        previous_id, secret = item.split(":", 1)
        if previous_id.strip().lower() in _DEV_KEY_IDS:
            raise KeyConfigurationError("生产环境不能保留开发密钥")
        _require_secret(secret)


def rotate_sealed(
    ring: KeyRing,
    sealed: str,
    *,
    user_id: str,
    connector_id: str,
    credential_type: str,
) -> str:
    """先解开旧密文，再写成活动密钥。解不开时不生成替换密文。"""
    plain = ring.decrypt(sealed, user_id=user_id, connector_id=connector_id, credential_type=credential_type)
    fresh = ring.encrypt(plain, user_id=user_id, connector_id=connector_id, credential_type=credential_type)
    if ring.decrypt(fresh, user_id=user_id, connector_id=connector_id, credential_type=credential_type) != plain:
        raise KeyConfigurationError("轮换校验失败")
    return fresh


def _require_secret(secret: str) -> None:
    cleaned = secret.strip()
    if not cleaned:
        raise KeyConfigurationError("生产环境缺少密钥")
    if len(cleaned) < 32:
        raise KeyConfigurationError("生产环境密钥过短")
    if cleaned.lower() in _PLACEHOLDER_SECRETS:
        raise KeyConfigurationError("生产环境使用了占位密钥")


def _material(secret: str) -> bytes:
    cleaned = secret.strip()
    if len(cleaned) < 16:
        raise KeyConfigurationError("密钥过短")
    return hashlib.sha256(cleaned.encode("utf-8")).digest()


def _aad(user_id: str, connector_id: str, credential_type: str) -> bytes:
    return f"{user_id}|{connector_id}|{credential_type}".encode("utf-8")


def _parts(sealed: str) -> tuple[str, str, str, str]:
    parts = sealed.split(".")
    if len(parts) != 4:
        raise KeyConfigurationError("密文无效")
    return parts[0], parts[1], parts[2], parts[3]
