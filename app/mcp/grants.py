"""用户、连接器和工具三级授权。连接器授权不会自动放开全部工具。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.mixins import utcnow
from app.models.tables import McpAuthorizationAudit, McpConnectorGrant, McpToolGrant
from app.tools.stdio_access import stdio_access

ACTIVE_GRANTS: "GrantStore | None" = None
GRANTS_REQUIRED = False


@dataclass
class GrantAudit:
    user_id: str
    connector_id: str
    tool_name: str
    action: str
    at: datetime


class MemoryGrantStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._known: set[tuple[str, str]] = set()
        self._connectors: dict[tuple[str, str], datetime | None] = {}
        self._revoked_connectors: set[tuple[str, str]] = set()
        self._tools: dict[tuple[str, str, str], datetime | None] = {}
        self._revoked_tools: set[tuple[str, str, str]] = set()
        self.audits: list[GrantAudit] = []

    async def ensure_default(self, user_id: str, connector_id: str, tools: set[str]) -> None:
        async with self._lock:
            key = (user_id, connector_id)
            if key in self._known:
                return
            self._known.add(key)
            self._connectors[key] = None
            for tool in tools:
                self._tools[(user_id, connector_id, tool)] = None
            self._audit(user_id, connector_id, "*", "default")

    async def grant_tool(self, user_id: str, connector_id: str, tool_name: str, expires_at: datetime | None = None) -> None:
        async with self._lock:
            key = (user_id, connector_id)
            self._known.add(key)
            self._revoked_connectors.discard(key)
            if key not in self._connectors:
                self._connectors[key] = None
            tool_key = (user_id, connector_id, tool_name)
            self._revoked_tools.discard(tool_key)
            self._tools[tool_key] = expires_at
            self._audit(user_id, connector_id, tool_name, "grant_tool")

    async def revoke_connector(self, user_id: str, connector_id: str) -> None:
        async with self._lock:
            key = (user_id, connector_id)
            self._known.add(key)
            self._revoked_connectors.add(key)
            self._connectors.pop(key, None)
            for tool_key in list(self._tools):
                if tool_key[0] == user_id and tool_key[1] == connector_id:
                    self._tools.pop(tool_key, None)
                    self._revoked_tools.add(tool_key)
            self._audit(user_id, connector_id, "*", "revoke_connector")

    async def revoke_tool(self, user_id: str, connector_id: str, tool_name: str) -> None:
        async with self._lock:
            self._known.add((user_id, connector_id))
            tool_key = (user_id, connector_id, tool_name)
            self._tools.pop(tool_key, None)
            self._revoked_tools.add(tool_key)
            self._audit(user_id, connector_id, tool_name, "revoke_tool")

    async def permitted(self, user_id: str, connector_id: str, tool_name: str) -> bool:
        async with self._lock:
            return self._permitted(user_id, connector_id, tool_name)

    async def tool_names(self, user_id: str, connector_id: str) -> set[str]:
        async with self._lock:
            names = set()
            for owner, connector, tool in self._tools:
                if owner == user_id and connector == connector_id and self._permitted(owner, connector, tool):
                    names.add(tool)
            return names

    async def known(self, user_id: str, connector_id: str) -> bool:
        async with self._lock:
            return (user_id, connector_id) in self._known

    def snapshot(self) -> dict[str, object]:
        return {
            "known": sorted(self._known),
            "connectors": {f"{user}\n{connector}": expires.isoformat() if expires else "" for (user, connector), expires in self._connectors.items()},
            "revoked_connectors": sorted(self._revoked_connectors),
            "tools": {
                f"{user}\n{connector}\n{tool}": expires.isoformat() if expires else ""
                for (user, connector, tool), expires in self._tools.items()
            },
            "revoked_tools": sorted(self._revoked_tools),
            "audits": [
                {
                    "user_id": item.user_id,
                    "connector_id": item.connector_id,
                    "tool_name": item.tool_name,
                    "action": item.action,
                    "at": item.at.isoformat(),
                }
                for item in self.audits
            ],
        }

    @classmethod
    def restore(cls, snapshot: dict[str, object]) -> "MemoryGrantStore":
        store = cls()
        known = snapshot.get("known")
        if isinstance(known, list):
            store._known = {tuple(item) for item in known if isinstance(item, (list, tuple)) and len(item) == 2}
        connectors = snapshot.get("connectors")
        if isinstance(connectors, dict):
            for raw, expires in connectors.items():
                user, connector = str(raw).split("\n", 1)
                store._connectors[(user, connector)] = _parse_time(expires)
        tools = snapshot.get("tools")
        if isinstance(tools, dict):
            for raw, expires in tools.items():
                user, connector, tool = str(raw).split("\n")
                store._tools[(user, connector, tool)] = _parse_time(expires)
        revoked_connectors = snapshot.get("revoked_connectors")
        if isinstance(revoked_connectors, list):
            store._revoked_connectors = {tuple(item) for item in revoked_connectors if isinstance(item, (list, tuple)) and len(item) == 2}
        revoked_tools = snapshot.get("revoked_tools")
        if isinstance(revoked_tools, list):
            store._revoked_tools = {tuple(item) for item in revoked_tools if isinstance(item, (list, tuple)) and len(item) == 3}
        saved_audits = snapshot.get("audits")
        if isinstance(saved_audits, list):
            for item in saved_audits:
                if not isinstance(item, dict):
                    continue
                at = item.get("at")
                store.audits.append(
                    GrantAudit(
                        str(item.get("user_id") or ""),
                        str(item.get("connector_id") or ""),
                        str(item.get("tool_name") or ""),
                        str(item.get("action") or ""),
                        _parse_time(at) or utcnow(),
                    )
                )
        return store

    def _permitted(self, user_id: str, connector_id: str, tool_name: str) -> bool:
        key = (user_id, connector_id)
        if key in self._revoked_connectors or key not in self._connectors:
            return False
        if _expired(self._connectors[key]):
            return False
        tool_key = (user_id, connector_id, tool_name)
        if tool_key in self._revoked_tools or tool_key not in self._tools:
            return False
        return not _expired(self._tools[tool_key])

    def _audit(self, user_id: str, connector_id: str, tool_name: str, action: str) -> None:
        self.audits.append(GrantAudit(user_id, connector_id, tool_name, action, utcnow()))


class GrantStore:
    async def ensure_default(self, user_id: str, connector_id: str, tools: set[str]) -> None:
        raise NotImplementedError

    async def grant_tool(self, user_id: str, connector_id: str, tool_name: str, expires_at: datetime | None = None) -> None:
        raise NotImplementedError

    async def revoke_connector(self, user_id: str, connector_id: str) -> None:
        raise NotImplementedError

    async def revoke_tool(self, user_id: str, connector_id: str, tool_name: str) -> None:
        raise NotImplementedError

    async def permitted(self, user_id: str, connector_id: str, tool_name: str) -> bool:
        raise NotImplementedError

    async def tool_names(self, user_id: str, connector_id: str) -> set[str]:
        raise NotImplementedError

    async def known(self, user_id: str, connector_id: str) -> bool:
        raise NotImplementedError


class SqlGrantStore(GrantStore):
    """数据库授权。调用方传入会话工厂，每次操作自己提交。"""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def ensure_default(self, user_id: str, connector_id: str, tools: set[str]) -> None:
        async with self._factory() as session:
            if await self._connector(session, user_id, connector_id) is not None:
                return
            session.add(McpConnectorGrant(user_id=_user(user_id), connector_id=connector_id))
            for tool in tools:
                session.add(McpToolGrant(user_id=_user(user_id), connector_id=connector_id, tool_name=tool))
            _add_audit(session, user_id, connector_id, "*", "default")
            await session.commit()

    async def grant_tool(self, user_id: str, connector_id: str, tool_name: str, expires_at: datetime | None = None) -> None:
        async with self._factory() as session:
            connector = await self._connector(session, user_id, connector_id)
            if connector is None:
                session.add(McpConnectorGrant(user_id=_user(user_id), connector_id=connector_id))
            else:
                connector.revoked_at = None
            tool = await self._tool(session, user_id, connector_id, tool_name)
            if tool is None:
                session.add(
                    McpToolGrant(
                        user_id=_user(user_id),
                        connector_id=connector_id,
                        tool_name=tool_name,
                        expires_at=expires_at,
                    )
                )
            else:
                tool.revoked_at = None
                tool.expires_at = expires_at
            _add_audit(session, user_id, connector_id, tool_name, "grant_tool")
            await session.commit()

    async def revoke_connector(self, user_id: str, connector_id: str) -> None:
        async with self._factory() as session:
            connector = await self._connector(session, user_id, connector_id)
            now = utcnow()
            if connector is None:
                session.add(McpConnectorGrant(user_id=_user(user_id), connector_id=connector_id, revoked_at=now))
            else:
                connector.revoked_at = now
            tools = await session.scalars(
                select(McpToolGrant).where(
                    McpToolGrant.user_id == _user(user_id),
                    McpToolGrant.connector_id == connector_id,
                )
            )
            for tool in tools:
                tool.revoked_at = now
            _add_audit(session, user_id, connector_id, "*", "revoke_connector")
            await session.commit()

    async def revoke_tool(self, user_id: str, connector_id: str, tool_name: str) -> None:
        async with self._factory() as session:
            tool = await self._tool(session, user_id, connector_id, tool_name)
            now = utcnow()
            if tool is None:
                session.add(
                    McpToolGrant(
                        user_id=_user(user_id),
                        connector_id=connector_id,
                        tool_name=tool_name,
                        revoked_at=now,
                    )
                )
            else:
                tool.revoked_at = now
            _add_audit(session, user_id, connector_id, tool_name, "revoke_tool")
            await session.commit()

    async def permitted(self, user_id: str, connector_id: str, tool_name: str) -> bool:
        async with self._factory() as session:
            connector = await self._connector(session, user_id, connector_id)
            tool = await self._tool(session, user_id, connector_id, tool_name)
            return _row_open(connector) and _row_open(tool)

    async def tool_names(self, user_id: str, connector_id: str) -> set[str]:
        async with self._factory() as session:
            if not _row_open(await self._connector(session, user_id, connector_id)):
                return set()
            rows = await session.scalars(
                select(McpToolGrant).where(
                    McpToolGrant.user_id == _user(user_id),
                    McpToolGrant.connector_id == connector_id,
                )
            )
            return {row.tool_name for row in rows if _row_open(row)}

    async def known(self, user_id: str, connector_id: str) -> bool:
        async with self._factory() as session:
            return await self._connector(session, user_id, connector_id) is not None

    async def _connector(self, session: AsyncSession, user_id: str, connector_id: str) -> McpConnectorGrant | None:
        return await session.scalar(
            select(McpConnectorGrant).where(
                McpConnectorGrant.user_id == _user(user_id),
                McpConnectorGrant.connector_id == connector_id,
            )
        )

    async def _tool(self, session: AsyncSession, user_id: str, connector_id: str, tool_name: str) -> McpToolGrant | None:
        return await session.scalar(
            select(McpToolGrant).where(
                McpToolGrant.user_id == _user(user_id),
                McpToolGrant.connector_id == connector_id,
                McpToolGrant.tool_name == tool_name,
            )
        )


def bind_grants(store: GrantStore | None, *, production: bool = False) -> None:
    global ACTIVE_GRANTS, GRANTS_REQUIRED
    ACTIVE_GRANTS = store
    GRANTS_REQUIRED = production


async def sync_stdio(user_id: str, server: str) -> None:
    """把持久授权投影回进程内目录。没有记录时，默认只复制当前目录。"""
    store = ACTIVE_GRANTS
    if store is None:
        if GRANTS_REQUIRED:
            stdio_access.revoke(user_id)
            return
        stdio_access.ensure(user_id)
        return
    if stdio_access.has_user(user_id) and not await store.known(user_id, server):
        return
    prefix = f"mcp__{server}__"
    remotes = {name[len(prefix) :] for name in stdio_access.catalog() if name.startswith(prefix)}
    await store.ensure_default(user_id, server, remotes)
    allowed = await store.tool_names(user_id, server)
    stdio_access.grant(user_id, {f"{prefix}{name}" for name in allowed})


async def stdio_permitted(user_id: str | None, local_name: str) -> bool:
    if not user_id or not stdio_access.allows(user_id, local_name):
        return False
    store = ACTIVE_GRANTS
    if store is None:
        return not GRANTS_REQUIRED
    from app.mcp.execution import split_local

    parsed = split_local(local_name)
    if parsed is None:
        return False
    connector_id, tool_name = parsed
    try:
        if not await store.known(user_id, connector_id):
            return not GRANTS_REQUIRED
        return await store.permitted(user_id, connector_id, tool_name)
    except Exception:
        return False


def _user(value: str) -> UUID:
    return UUID(value)


def _row_open(row: object) -> bool:
    if row is None or getattr(row, "revoked_at", None) is not None:
        return False
    return not _expired(getattr(row, "expires_at", None))


def _add_audit(session: AsyncSession, user_id: str, connector_id: str, tool_name: str, action: str) -> None:
    session.add(
        McpAuthorizationAudit(
            user_id=_user(user_id),
            connector_id=connector_id,
            tool_name=tool_name,
            action=action,
        )
    )


def _expired(value: datetime | None) -> bool:
    return value is not None and value <= utcnow()


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value)
