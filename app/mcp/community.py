"""官方 MCP Registry 社区目录客户端。只暴露远程（remote）类型的连接器。

数据源：https://registry.modelcontextprotocol.io （无需认证，v0.1 接口已冻结）。
只返回带 remotes 且 URL 不含模板变量（{...}）的条目，避免引入本地包（stdio）执行风险。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("muzi_llm")

# 官方 registry 的默认地址。国内访问慢时，可通过 MCP_REGISTRY_BASE_URL 指向自建镜像或加速代理。
DEFAULT_REGISTRY_BASE = "https://registry.modelcontextprotocol.io"
_LIST_PATH = "/v0.1/servers"

# 跨请求的进程内缓存：键为 (base_url, query, limit)，值 (时间戳, 结果)。
# 目录是公共数据，缓存命中即可免跨境请求，TTL 放长可显著降低访问延迟。
_CACHE: dict[tuple[str, str, int], tuple[float, list[dict[str, str]]]] = {}
_CACHE_TTL_SECONDS = 1800.0
# 官方 registry API 在高峰窗口可达 20–45s，超时放宽，避免高峰期必失败。
_TIMEOUT_SECONDS = 30.0

# awesome-mcp.tools 聚合目录（GitHub stars / mcp.so / glama / mcpservers.com）。
# 提供官方 registry 缺失的两项能力：全文搜索（name+描述+标签）与热度指标（GitHub stars）。
AWESOME_API_BASE = "https://awesome-mcp.tools/api/servers"
_AWESOME_TIMEOUT_SECONDS = 30.0
# awesome 的排序语义：空串（默认，按 stars）、trending（24h 增长）、hot（热门）。
# 实测默认排序不稳定（偶发 0 星排前），stars 排序由后端按 stars 字段自行降序保证。
_AWESOME_SORTS = {"stars": "", "trending": "trending", "hot": "hot"}
# 交叉匹配：awesome 条目（owner/repo）→ 官方 registry 的 remote 端点。缓存键为 repo 名。
_REMOTE_CACHE: dict[str, tuple[float, dict[str, str] | None]] = {}
_REMOTE_CACHE_TTL_SECONDS = 3600.0
# awesome 结果缓存：键为 (base_url, query, norm_sort, limit, page)。
_AWESOME_CACHE: dict[tuple[str, str, str, int, int], tuple[float, tuple[list[dict[str, Any]], int]]] = {}
_AWESOME_CACHE_TTL_SECONDS = 1800.0
# 交叉匹配的并发上限，避免对官方 registry 造成瞬时压力。
_REMOTE_CONCURRENCY = 8

_TEMPLATE_VAR = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
_QUALIFIED_NAME = re.compile(r"^[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+$")


def slug_connector_name(qualified_name: str) -> str:
    """把官方 registry 的 reverse-DNS 名称折叠成平台内部短名。

    结果满足 ``^[a-z][a-z0-9_]{0,31}$``，可直接用作 connector_name。
    """
    raw = re.sub(r"[^a-z0-9_]+", "_", qualified_name.lower()).strip("_")
    if not raw or not raw[0].isalpha():
        raw = "s_" + raw
    return raw[:32]


def _transport(remote_type: str) -> str | None:
    if remote_type == "streamable-http":
        return "streamable_http"
    if remote_type == "sse":
        return "sse"
    return None


def _plain_url(value: str) -> str | None:
    """只接受不含模板变量的纯 http(s) URL。"""
    text = value.strip()
    if not text or _TEMPLATE_VAR.search(text):
        return None
    parsed = urlparse(text)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return text


def _display_name(item: dict[str, Any], fallback: str) -> str:
    title = item.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()[:100]
    return fallback


def _icon(item: dict[str, Any]) -> str:
    icons = item.get("icons")
    if not isinstance(icons, list):
        return ""
    for entry in icons:
        if isinstance(entry, dict) and isinstance(entry.get("src"), str):
            return entry["src"][:255]
    return ""


def _updated_at(item: dict[str, Any]) -> str:
    """提取 registry 条目的更新时间（RFC3339），供前端排序。缺失返回空串。"""
    for key in ("updatedAt", "createdAt"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:64]
    return ""


def _extract(server: dict[str, Any]) -> dict[str, str] | None:
    name = server.get("name")
    if not isinstance(name, str) or _QUALIFIED_NAME.fullmatch(name) is None:
        return None
    remotes = server.get("remotes")
    if not isinstance(remotes, list) or not remotes:
        return None
    for remote in remotes:
        if not isinstance(remote, dict):
            continue
        transport = _transport(str(remote.get("type") or ""))
        url = _plain_url(str(remote.get("url") or ""))
        if transport is None or url is None:
            continue
        description = server.get("description")
        if not isinstance(description, str):
            description = ""
        website_url = server.get("websiteUrl")
        if not isinstance(website_url, str):
            website_url = ""
        return {
            "qualified_name": name,
            "display_name": _display_name(server, name.rsplit("/", 1)[-1]),
            "description": description[:200],
            "transport": transport,
            "url": url,
            "icon_url": _icon(server),
            "website_url": website_url[:255],
            "updated_at": _updated_at(server),
        }
    return None


async def search_remote_servers(
    query: str,
    limit: int,
    *,
    base_url: str = DEFAULT_REGISTRY_BASE,
) -> list[dict[str, str]]:
    """搜索官方 registry，返回远程类型连接器列表。失败时返回空列表（不抛异常）。

    base_url 指向 registry 根地址；国内访问慢时可换成自建镜像或加速代理，
    配合 TTL 缓存与放宽的超时，规避跨境链路与官方高峰窗口的延迟。
    """
    # 官方 registry 的 limit 上限为 100，超过会返回 422。
    limit = max(1, min(limit, 100))
    base = (base_url or DEFAULT_REGISTRY_BASE).rstrip("/")
    key = (base, query.strip(), limit)
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    results: list[dict[str, str]] = []
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, follow_redirects=False) as client:
            response = await client.get(
                f"{base}{_LIST_PATH}",
                params={"search": query.strip(), "limit": limit, "version": "latest"},
                headers={"Accept": "application/json"},
            )
            if response.status_code >= 400:
                logger.warning("mcp community registry HTTP %s", response.status_code)
                return results
            body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("mcp community registry unavailable: %s", exc)
        return results

    servers = body.get("servers") if isinstance(body, dict) else None
    if not isinstance(servers, list):
        return results
    seen: set[str] = set()
    for entry in servers:
        if not isinstance(entry, dict):
            continue
        detail = entry.get("server") if isinstance(entry.get("server"), dict) else entry
        # 官方 registry 把 updatedAt 放在外层 entry 的 _meta 里，合并进 detail 供提取排序时间。
        if isinstance(detail, dict):
            merged = dict(detail)
            meta = entry.get("_meta")
            if isinstance(meta, dict):
                for meta_val in meta.values():
                    if isinstance(meta_val, dict):
                        for key in ("updatedAt", "createdAt", "publishedAt"):
                            if key not in merged and isinstance(meta_val.get(key), str):
                                merged[key] = meta_val[key]
                        break
            detail = merged
        item = _extract(detail)
        if item is None or item["qualified_name"] in seen:
            continue
        seen.add(item["qualified_name"])
        results.append(item)
    _CACHE[key] = (now, results)
    return results


def _awesome_sort(sort: str) -> str:
    """把前端排序关键词映射为 awesome 的 sort 参数。未知值回退到默认（stars）。"""
    return _AWESOME_SORTS.get(sort, "")


def _awesome_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    """把 awesome 条目标准化。要求 name 形如 owner/repo。"""
    name = raw.get("name")
    if not isinstance(name, str) or "/" not in name:
        return None
    stars = raw.get("stars")
    if not isinstance(stars, int):
        stars = 0
    description = raw.get("description") or raw.get("about") or ""
    if not isinstance(description, str):
        description = ""
    source_url = raw.get("sourceUrl") or ""
    if not isinstance(source_url, str):
        source_url = ""
    website_url = raw.get("website") or ""
    if not isinstance(website_url, str):
        website_url = ""
    repo_name = name.rsplit("/", 1)[-1]
    owner = name.split("/", 1)[0]
    # awesome 条目本身不带图标，用 GitHub 组织/作者头像作为默认图标
    # （https://github.com/{owner}.png 公开可用，302 到 avatars.githubusercontent.com）。
    # 交叉匹配到官方 registry 且有官方 icon 时会优先覆盖。
    return {
        "qualified_name": name,
        "display_name": repo_name,
        "description": description.strip()[:200],
        "stars": stars,
        "is_hot": bool(raw.get("isHot")),
        "language": (raw.get("language") or ""),
        "category": (raw.get("category") or ""),
        "source_url": source_url[:255],
        "website_url": website_url[:255],
        "icon_url": f"https://github.com/{owner}.png",
        "transport": "",
        "url": "",
        "updated_at": "",
        # 内部字段，用于交叉匹配，最终返回前删除
        "repo_name": repo_name,
        "owner": owner,
    }


async def _find_official_remote(
    client: httpx.AsyncClient,
    repo_name: str,
    owner: str,
    base: str,
) -> dict[str, str] | None:
    """用仓库名交叉查官方 registry，返回可连接的 remote 端点信息（或 None）。

    匹配规则：官方条目 name 的仓库段须与 awesome 的 repo 名一致、且至少含一个
    streamable-http / sse 端点，并且官方条目的 namespace 必须与 awesome 的 owner
    强相关（owner 出现在 namespace 中，或 namespace 的某个域名段等于 owner）。
    避免把 ``github/github-mcp-server`` 这类知名仓库错误关联到第三方同名代理服务。
    """
    key = repo_name.lower()
    now = time.monotonic()
    cached = _REMOTE_CACHE.get(key)
    if cached is not None and now - cached[0] < _REMOTE_CACHE_TTL_SECONDS:
        return cached[1]

    result: dict[str, str] | None = None
    try:
        response = await client.get(
            f"{base}{_LIST_PATH}",
            params={"search": repo_name, "limit": 20, "version": "latest"},
            headers={"Accept": "application/json"},
        )
        if response.status_code >= 400:
            _REMOTE_CACHE[key] = (now, None)
            return None
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("official remote lookup failed for %s: %s", repo_name, exc)
        _REMOTE_CACHE[key] = (now, None)
        return None

    servers = body.get("servers") if isinstance(body, dict) else None
    if not isinstance(servers, list):
        _REMOTE_CACHE[key] = (now, None)
        return None

    owner_lower = owner.lower()
    candidates: list[dict[str, str]] = []
    for entry in servers:
        if not isinstance(entry, dict):
            continue
        detail = entry.get("server") if isinstance(entry.get("server"), dict) else entry
        name = detail.get("name")
        if not isinstance(name, str):
            continue
        if name.rsplit("/", 1)[-1].lower() != repo_name.lower():
            continue
        namespace = name.rsplit("/", 1)[0].lower()
        # 仅当 namespace 与 owner 强相关才视为同源，否则是第三方同名服务，跳过。
        if not _namespace_matches_owner(namespace, owner_lower):
            continue
        remotes = detail.get("remotes")
        if not isinstance(remotes, list):
            continue
        for remote in remotes:
            if not isinstance(remote, dict):
                continue
            transport = _transport(str(remote.get("type") or ""))
            url = _plain_url(str(remote.get("url") or ""))
            if transport is None or url is None:
                continue
            candidates.append(
                {
                    "qualified_name": name,
                    "display_name": _display_name(detail, name.rsplit("/", 1)[-1]),
                    "transport": transport,
                    "url": url,
                    "icon_url": _icon(detail),
                    "website_url": (str(detail.get("websiteUrl") or ""))[:255],
                }
            )
            break  # 每个官方条目只取第一个可用 remote

    if candidates:
        result = candidates[0]
    _REMOTE_CACHE[key] = (now, result)
    return result


def _namespace_matches_owner(namespace: str, owner_lower: str) -> bool:
    """判断官方 registry 的 reverse-DNS namespace 是否与 GitHub owner 同源。"""
    if not owner_lower:
        return False
    if owner_lower in namespace:
        return True
    # reverse-DNS 分段：namespace 的任意一个域名段等于 owner（如 com.github ↔ github）。
    return owner_lower in {part for part in re.split(r"[.\-_]", namespace) if part}


async def _search_installable(
    query: str,
    limit: int,
    page: int,
    *,
    base: str,
) -> dict[str, Any]:
    """「可安装」模式：官方 registry 的远程连接器（全部带 remote 端点），按最近更新排序。

    官方 registry 不提供 stars / 下载量等热度字段，因此此模式无热度排序，
    只能按更新时间降序，保证列表内全部可一键安装、无「查看源码」条目。
    """
    servers = await search_remote_servers(query, 100, base_url=base)
    servers.sort(key=lambda s: s.get("updated_at") or "", reverse=True)
    total = len(servers)
    start = (page - 1) * limit
    items = servers[start : start + limit]
    return {"servers": items, "total": total, "page": page, "limit": limit}


async def search_community_hybrid(
    query: str,
    sort: str,
    limit: int,
    page: int,
    *,
    base_url: str = DEFAULT_REGISTRY_BASE,
    installable_only: bool = False,
) -> dict[str, Any]:
    """社区目录混合搜索。

    - ``installable_only=True``：只返回官方 registry 的远程连接器（全部可一键安装），
      按最近更新时间降序；无 GitHub stars 热度（官方不提供该字段）。
    - ``installable_only=False``：awesome 全文搜索 + GitHub stars 热度排序 + 交叉匹配
      官方 remote 端点；本地包（stdio）无端点，前端降级为「查看源码」。

    返回 ``{"servers": [...], "total": int, "page": int, "limit": int}``。
    """
    limit = max(1, min(limit, 100))
    page = max(1, page)
    base = (base_url or DEFAULT_REGISTRY_BASE).rstrip("/")

    if installable_only:
        return await _search_installable(query, limit, page, base=base)

    norm_sort = _awesome_sort(sort)
    # stars 排序时多拉一些（最多 100）再在后端取 top，规避 awesome 默认排序
    # 混入 0 星 / 低星条目，保证热门榜是真正按 stars 靠前的服务。
    fetch_limit = min(max(limit * 4, 20), 100) if sort == "stars" else limit

    # 1) 取 awesome 目录（带缓存）。
    key = (base, query.strip(), norm_sort, fetch_limit, page)
    now = time.monotonic()
    cached = _AWESOME_CACHE.get(key)
    if cached is not None and now - cached[0] < _AWESOME_CACHE_TTL_SECONDS:
        items, total = cached[1]
    else:
        items: list[dict[str, Any]] = []
        total = 0
        try:
            async with httpx.AsyncClient(timeout=_AWESOME_TIMEOUT_SECONDS, follow_redirects=False) as client:
                response = await client.get(
                    AWESOME_API_BASE,
                    params={"q": query.strip(), "sort": norm_sort, "limit": fetch_limit, "page": page},
                    headers={"Accept": "application/json"},
                )
                if response.status_code >= 400:
                    logger.warning("awesome-mcp.tools HTTP %s", response.status_code)
                    return {"servers": [], "total": 0, "page": page, "limit": limit}
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("awesome-mcp.tools unavailable: %s", exc)
            return {"servers": [], "total": 0, "page": page, "limit": limit}

        if isinstance(body, dict):
            raw_items = body.get("items")
            meta = body.get("meta")
            if isinstance(raw_items, list):
                items = [_awesome_item(r) for r in raw_items if isinstance(r, dict)]
                items = [it for it in items if it is not None]
            if isinstance(meta, dict):
                total = int(meta.get("total") or 0)
        _AWESOME_CACHE[key] = (now, (items, total))

    # 2) 并发交叉匹配官方 remote 端点。
    # 浅拷贝后再修改，避免后续 pop 内部字段污染缓存中的原始条目。
    items = [dict(it) for it in items]
    if items:
        sem = asyncio.Semaphore(_REMOTE_CONCURRENCY)

        async def match(item: dict[str, Any]) -> dict[str, Any]:
            async with sem:
                remote = await _find_official_remote(
                    client, item["repo_name"], item["owner"], base
                )
            if remote:
                item["qualified_name"] = remote["qualified_name"]
                item["display_name"] = remote["display_name"] or item["display_name"]
                item["transport"] = remote["transport"]
                item["url"] = remote["url"]
                # 官方 icon 存在时才覆盖，否则保留 GitHub 头像作为回退。
                if remote["icon_url"]:
                    item["icon_url"] = remote["icon_url"]
                item["website_url"] = remote["website_url"] or item["website_url"]
            return item

        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, follow_redirects=False) as client:
            items = list(await asyncio.gather(*(match(it) for it in items)))

    # 3) stars 排序由后端保证（awesome 默认排序不稳定）。
    if sort == "stars":
        items.sort(key=lambda it: int(it.get("stars") or 0), reverse=True)
        items = items[:limit]

    # 4) 清理内部字段。
    for it in items:
        it.pop("repo_name", None)
        it.pop("owner", None)

    return {"servers": items, "total": total, "page": page, "limit": limit}
