"""用 MCP_TOKEN_KEY 密封令牌。密文带随机数和校验，不能从中还原出密钥。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets


class TokenCipher:
    def __init__(self, key: str) -> None:
        cleaned = key.strip()
        if len(cleaned) < 16:
            raise ValueError("MCP_TOKEN_KEY 过短")
        self._key = hashlib.sha256(cleaned.encode("utf-8")).digest()

    def encrypt(self, plain: str) -> str:
        nonce = secrets.token_bytes(16)
        data = plain.encode("utf-8")
        body = bytes(left ^ right for left, right in zip(data, _stream(self._key, nonce, len(data))))
        mac = hmac.new(self._key, nonce + body, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(nonce + mac + body).decode("ascii")

    def decrypt(self, sealed: str) -> str:
        raw = base64.urlsafe_b64decode(sealed.encode("ascii"))
        if len(raw) < 48:
            raise ValueError("密文无效")
        nonce, mac, body = raw[:16], raw[16:48], raw[48:]
        expected = hmac.new(self._key, nonce + body, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expected):
            raise ValueError("密文无效")
        plain = bytes(left ^ right for left, right in zip(body, _stream(self._key, nonce, len(body))))
        return plain.decode("utf-8")


def _stream(key: bytes, nonce: bytes, size: int) -> bytes:
    chunks: list[bytes] = []
    counter = 0
    while sum(len(item) for item in chunks) < size:
        chunks.append(hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest())
        counter += 1
    return b"".join(chunks)[:size]
