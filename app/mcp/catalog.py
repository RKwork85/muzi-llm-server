"""服务端 MCP 目录。地址只来自这个文件。"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,16}$")
_VAR = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
_FILE = Path(__file__).with_name("connectors.toml")


@dataclass(frozen=True)
class Connector:
    name: str
    display_name: str
    transport: str
    url: str
    allow_write: bool = False
    issuer: str = ""


def load_catalog(
    path: Path | None = None,
    *,
    allow_insecure: bool = False,
    env: dict[str, str] | None = None,
) -> tuple[Connector, ...]:
    source = path or _FILE
    raw = tomllib.loads(source.read_text(encoding="utf-8"))
    items: list[Connector] = []
    for name, spec in raw.items():
        if not isinstance(spec, dict) or _NAME.fullmatch(name) is None:
            continue
        transport = str(spec.get("transport") or "")
        url = _expand(str(spec.get("url") or ""), env)
        if transport not in {"streamable_http", "sse"} or not _allowed_url(url, allow_insecure):
            continue
        title = str(spec.get("display_name") or name).strip()[:40] or name
        issuer = _expand(str(spec.get("issuer") or ""), env)
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


def _expand(value: str, env: dict[str, str] | None = None) -> str:
    """展开 ${VAR} / $VAR 模板。优先用传入的 env，其次回退到进程环境变量，未设置的变量原样保留。"""
    mapping = dict(os.environ)
    if env:
        mapping.update(env)
    return _VAR.sub(lambda m: mapping.get(m.group(1) or m.group(2) or "", m.group(0)), value.strip())
