"""web_fetch 的本地 URL 预校验和来源集合。不解析 DNS，不发起连接。"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit

from app.core.config import Settings

_CREDENTIAL_KEYS = frozenset(
    {
        "token",
        "access_token",
        "key",
        "api_key",
        "secret",
        "password",
        "sig",
        "signature",
        "auth",
        "session",
    }
)
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"']+")
_ALLOWED_PORTS = frozenset({80, 443})


class UrlRejected(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class UrlLedger:
    """一次运行内出现过的 URL。不跨运行共享。"""

    def __init__(self, message: str, *, limit: int = 32) -> None:
        self.allowed: set[str] = set()
        self._limit = limit
        for found in _URL_IN_TEXT.findall(message):
            self.add(found)

    def add(self, url: str) -> None:
        if len(self.allowed) >= self._limit:
            return
        try:
            normalized = normalize_url(url)
        except UrlRejected:
            return
        self.allowed.add(normalized)

    def permits(self, url: str) -> bool:
        try:
            normalized = normalize_url(url)
        except UrlRejected:
            return False
        return normalized in self.allowed


def normalize_url(url: str) -> str:
    """小写 scheme/host，去掉 fragment 和默认端口。不读取配置。"""
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise UrlRejected("web_invalid_request")
    host = parts.hostname.lower()
    port = parts.port
    if port in {None, 80, 443} and (
        (parts.scheme == "https" and port in {None, 443}) or (parts.scheme == "http" and port in {None, 80})
    ):
        netloc = host
    else:
        netloc = f"{host}:{port}"
    return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))


def validate_fetch_url(url: str, settings: Settings) -> str:
    if not isinstance(url, str) or len(url) > 2048 or "\x00" in url:
        raise UrlRejected("web_invalid_request")
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise UrlRejected("web_invalid_request")
    if parts.username is not None or parts.password is not None:
        raise UrlRejected("web_invalid_request")
    host = parts.hostname.lower()
    if _is_blocked_host(host):
        raise UrlRejected("web_invalid_request")
    port = parts.port
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    if port not in _ALLOWED_PORTS:
        raise UrlRejected("web_invalid_request")
    if settings.web_fetch_block_credential_query and _query_has_secret(parts.query):
        raise UrlRejected("web_invalid_request")
    denied = _domains(settings.web_fetch_denied_domains)
    allowed = _domains(settings.web_fetch_allowed_domains)
    if any(host == item or host.endswith("." + item) for item in denied):
        raise UrlRejected("web_invalid_request")
    if settings.web_fetch_url_policy == "allowlist":
        if not allowed or not any(host == item or host.endswith("." + item) for item in allowed):
            raise UrlRejected("web_invalid_request")
    path = parts.path or "/"
    netloc = host if port in {80, 443} and (
        (parts.scheme == "https" and port == 443) or (parts.scheme == "http" and port == 80)
    ) else f"{host}:{port}"
    if (parts.scheme == "https" and port == 443) or (parts.scheme == "http" and port == 80):
        netloc = host
    rebuilt = urlunsplit((parts.scheme, netloc, path, parts.query, ""))
    if len(rebuilt) > 2048:
        raise UrlRejected("web_invalid_request")
    return rebuilt


def _is_blocked_host(host: str) -> bool:
    if host in {"localhost"} or host.endswith(".localhost") or host.endswith(".local"):
        return True
    if host.endswith(".internal") or host.endswith(".lan") or "." not in host:
        return True
    address = _ip_literal(host)
    if address is None:
        return False
    return not address.is_global


def _ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    text = host.strip("[]")
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        pass
    if re.fullmatch(r"0x[0-9a-fA-F]+", text):
        return ipaddress.IPv4Address(int(text, 16))
    if re.fullmatch(r"[0-9]+", text):
        try:
            return ipaddress.IPv4Address(int(text, 10))
        except ipaddress.AddressValueError:
            return None
    if re.fullmatch(r"(0x[0-9a-fA-F]+|\d+)(\.(0x[0-9a-fA-F]+|\d+)){3}", text):
        octets: list[int] = []
        for part in text.split("."):
            base = 16 if part.lower().startswith("0x") else 8 if part.startswith("0") and part != "0" else 10
            try:
                value = int(part, base)
            except ValueError:
                return None
            if value > 255:
                return None
            octets.append(value)
        try:
            return ipaddress.IPv4Address(".".join(str(item) for item in octets))
        except ipaddress.AddressValueError:
            return None
    return None


def _query_has_secret(query: str) -> bool:
    for piece in query.split("&"):
        name = piece.split("=", 1)[0].lower()
        if name in _CREDENTIAL_KEYS:
            return True
    return False


def _domains(raw: str) -> tuple[str, ...]:
    items: list[str] = []
    for part in raw.split(","):
        item = part.strip().lower().rstrip(".")
        if item and item not in items:
            items.append(item)
    return tuple(items)
