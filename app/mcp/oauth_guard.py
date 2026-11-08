"""OAuth 端点的可信边界。Issuer 来自目录，不来自未核对的元数据。"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

Resolver = Callable[[str], Awaitable[list[ipaddress.IPv4Address | ipaddress.IPv6Address]]]

_EXTRA_BLOCKED = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("2001:db8::/32"),
)


class OAuthFlowError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def trusted_issuer(resource_url: str, configured: str) -> str:
    """可信 Issuer 只来自目录。未配置时用资源 URL 的 origin，不读取元数据。"""
    chosen = configured.strip() or _origin(resource_url)
    _require_issuer_shape(chosen)
    return chosen


def issuers_equal(expected: str, actual: str) -> bool:
    """精确匹配。不使用子串、前缀或大小写折叠。"""
    if not isinstance(expected, str) or not isinstance(actual, str):
        return False
    if expected != actual:
        return False
    left = urlsplit(expected)
    right = urlsplit(actual)
    if left.username or left.password or right.username or right.password:
        return False
    if left.scheme != right.scheme or left.netloc != right.netloc or left.path != right.path:
        return False
    return left.scheme in {"https", "http"} and bool(left.hostname)


def metadata_url_for_issuer(issuer: str) -> str:
    parsed = urlsplit(issuer)
    path = parsed.path or ""
    base = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    if path and path != "/":
        suffix = path if path.startswith("/") else f"/{path}"
        return f"{base}/.well-known/oauth-authorization-server{suffix.rstrip('/')}"
    return f"{base}/.well-known/oauth-authorization-server"


def validate_endpoint_shape(url: str, *, allow_insecure: bool) -> str:
    parsed = urlsplit(url.strip())
    if parsed.username or parsed.password:
        raise OAuthFlowError("endpoint_userinfo")
    if parsed.scheme == "https":
        pass
    elif parsed.scheme == "http" and allow_insecure:
        pass
    else:
        raise OAuthFlowError("endpoint_scheme")
    if not parsed.hostname or parsed.hostname != parsed.hostname.strip():
        raise OAuthFlowError("endpoint_host")
    if any(char in parsed.hostname for char in (" ", "\\", "@")):
        raise OAuthFlowError("endpoint_host")
    return url.strip()


def addresses_allowed(
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address],
    *,
    allow_insecure: bool,
) -> bool:
    if not addresses:
        return False
    for raw in addresses:
        ip = raw.ipv4_mapped if isinstance(raw, ipaddress.IPv6Address) and raw.ipv4_mapped else raw
        if ip.is_unspecified or ip.is_multicast or ip.is_link_local or ip.is_reserved:
            return False
        if any(ip in network for network in _EXTRA_BLOCKED):
            return False
        if ip.is_loopback:
            if not allow_insecure:
                return False
            continue
        if ip.is_private:
            return False
    return True


def literal_addresses(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address] | None:
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        return None


async def system_resolver(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    def lookup() -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        found: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
        for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM):
            address = ipaddress.ip_address(item[4][0])
            if address not in found:
                found.append(address)
        return found

    import asyncio

    try:
        return await asyncio.to_thread(lookup)
    except socket.gaierror as exc:
        raise OAuthFlowError("endpoint_dns") from exc


async def screen_endpoint(
    url: str,
    *,
    allow_insecure: bool,
    resolver: Resolver | None,
    allow_unresolved_name: bool,
) -> str:
    """在发送凭证之前检查端点。allow_unresolved_name 只给进程内 ASGI 测试。"""
    cleaned = validate_endpoint_shape(url, allow_insecure=allow_insecure)
    host = urlsplit(cleaned).hostname or ""
    literal = literal_addresses(host)
    if literal is not None:
        addresses = literal
    elif resolver is not None:
        addresses = await resolver(host)
    elif allow_unresolved_name:
        return cleaned
    else:
        addresses = await system_resolver(host)
    if not addresses_allowed(addresses, allow_insecure=allow_insecure):
        raise OAuthFlowError("endpoint_address")
    return cleaned


def _origin(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise OAuthFlowError("issuer_anchor")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _require_issuer_shape(issuer: str) -> None:
    parsed = urlsplit(issuer)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise OAuthFlowError("issuer_anchor")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise OAuthFlowError("issuer_anchor")
