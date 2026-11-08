"""服务端 MCP 目录。地址只来自这个文件。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

_NAME = __import__("re").compile(r"^[a-z][a-z0-9_]{0,16}$")
_FILE = Path(__file__).with_name("connectors.toml")


@dataclass(frozen=True)
class Connector:
    name: str
    display_name: str
    transport: str
    url: str
    allow_write: bool = False
    issuer: str = ""


def load_catalog(path: Path | None = None, *, allow_insecure: bool = False) -> tuple[Connector, ...]:
    source = path or _FILE
    raw = tomllib.loads(source.read_text(encoding="utf-8"))
    items: list[Connector] = []
    for name, spec in raw.items():
        if not isinstance(spec, dict) or _NAME.fullmatch(name) is None:
            continue
        transport = str(spec.get("transport") or "")
        url = str(spec.get("url") or "").strip()
        if transport not in {"streamable_http", "sse"} or not _allowed_url(url, allow_insecure):
            continue
        title = str(spec.get("display_name") or name).strip()[:40] or name
        issuer = str(spec.get("issuer") or "").strip()
        if issuer and not _allowed_url(issuer, allow_insecure):
            continue
        items.append(
            Connector(
                name=name,
                display_name=title,
                transport=transport,
                url=url,
                allow_write=bool(spec.get("allow_write")),
                issuer=issuer,
            )
        )
    return tuple(items)


def _allowed_url(url: str, allow_insecure: bool) -> bool:
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.hostname:
        return True
    return allow_insecure and parsed.scheme == "http" and parsed.hostname not in {None, ""}
