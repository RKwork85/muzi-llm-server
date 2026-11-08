"""用户连接的保存。生产用数据库，测试用内存。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mixins import utcnow
from app.models.tables import McpConnection, McpOAuthClient, McpOAuthState


@dataclass
class ClientRecord:
    connector_name: str
    client_id: str
    client_secret_encrypted: str | None = None


@dataclass
class StateRecord:
    state_hash: str
    user_id: str
    connector_name: str
    code_verifier_encrypted: str
    expires_at: datetime


@dataclass
class ConnectionRecord:
    user_id: str
    connector_name: str
    status: str
    access_token_encrypted: str
    refresh_token_encrypted: str | None
    expires_at: datetime | None
    selected_tools: list[str] = field(default_factory=list)


class MemoryStore:
    def __init__(self) -> None:
        self.clients: dict[str, ClientRecord] = {}
        self.states: dict[str, StateRecord] = {}
        self.connections: dict[tuple[str, str], ConnectionRecord] = {}

    async def get_client(self, name: str) -> ClientRecord | None:
        return self.clients.get(name)

    async def save_client(self, record: ClientRecord) -> None:
        self.clients[record.connector_name] = record

    async def save_state(self, record: StateRecord) -> None:
        self.states[record.state_hash] = record

    async def pop_state(self, state_hash: str) -> StateRecord | None:
        record = self.states.pop(state_hash, None)
        if record is None or record.expires_at <= utcnow():
            return None
        return record

    async def save_connection(self, record: ConnectionRecord) -> None:
        self.connections[(record.user_id, record.connector_name)] = record

    async def get_connection(self, user_id: str, name: str) -> ConnectionRecord | None:
        return self.connections.get((user_id, name))

    async def list_connections(self, user_id: str) -> list[ConnectionRecord]:
        return [item for (owner, _), item in self.connections.items() if owner == user_id]

    async def delete_connection(self, user_id: str, name: str) -> None:
        self.connections.pop((user_id, name), None)


class SqlStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_client(self, name: str) -> ClientRecord | None:
        row = await self.session.scalar(select(McpOAuthClient).where(McpOAuthClient.connector_name == name))
        if row is None:
            return None
        return ClientRecord(row.connector_name, row.client_id, row.client_secret_encrypted)

    async def save_client(self, record: ClientRecord) -> None:
        row = await self.session.scalar(
            select(McpOAuthClient).where(McpOAuthClient.connector_name == record.connector_name)
        )
        if row is None:
            self.session.add(
                McpOAuthClient(
                    connector_name=record.connector_name,
                    client_id=record.client_id,
                    client_secret_encrypted=record.client_secret_encrypted,
                )
            )
        else:
            row.client_id = record.client_id
            row.client_secret_encrypted = record.client_secret_encrypted
        await self.session.flush()

    async def save_state(self, record: StateRecord) -> None:
        self.session.add(
            McpOAuthState(
                state_hash=record.state_hash,
                user_id=uuid.UUID(record.user_id),
                connector_name=record.connector_name,
                code_verifier_encrypted=record.code_verifier_encrypted,
                expires_at=record.expires_at,
            )
        )
        await self.session.flush()

    async def pop_state(self, state_hash: str) -> StateRecord | None:
        row = await self.session.scalar(select(McpOAuthState).where(McpOAuthState.state_hash == state_hash))
        if row is None:
            return None
        await self.session.delete(row)
        await self.session.flush()
        if row.expires_at <= utcnow():
            return None
        return StateRecord(
            state_hash=row.state_hash,
            user_id=str(row.user_id),
            connector_name=row.connector_name,
            code_verifier_encrypted=row.code_verifier_encrypted,
            expires_at=row.expires_at,
        )

    async def save_connection(self, record: ConnectionRecord) -> None:
        user_id = uuid.UUID(record.user_id)
        row = await self.session.scalar(
            select(McpConnection).where(
                McpConnection.user_id == user_id,
                McpConnection.connector_name == record.connector_name,
            )
        )
        if row is None:
            self.session.add(
                McpConnection(
                    user_id=user_id,
                    connector_name=record.connector_name,
                    status=record.status,
                    access_token_encrypted=record.access_token_encrypted,
                    refresh_token_encrypted=record.refresh_token_encrypted,
                    expires_at=record.expires_at,
                    selected_tools=list(record.selected_tools),
                )
            )
        else:
            row.status = record.status
            row.access_token_encrypted = record.access_token_encrypted
            row.refresh_token_encrypted = record.refresh_token_encrypted
            row.expires_at = record.expires_at
            row.selected_tools = list(record.selected_tools)
        await self.session.flush()

    async def get_connection(self, user_id: str, name: str) -> ConnectionRecord | None:
        row = await self.session.scalar(
            select(McpConnection).where(
                McpConnection.user_id == uuid.UUID(user_id),
                McpConnection.connector_name == name,
            )
        )
        if row is None:
            return None
        return _connection(row)

    async def list_connections(self, user_id: str) -> list[ConnectionRecord]:
        rows = (
            await self.session.scalars(select(McpConnection).where(McpConnection.user_id == uuid.UUID(user_id)))
        ).all()
        return [_connection(row) for row in rows]

    async def delete_connection(self, user_id: str, name: str) -> None:
        await self.session.execute(
            delete(McpConnection).where(
                McpConnection.user_id == uuid.UUID(user_id),
                McpConnection.connector_name == name,
            )
        )
        await self.session.flush()


def _connection(row: McpConnection) -> ConnectionRecord:
    selected = row.selected_tools if isinstance(row.selected_tools, list) else []
    return ConnectionRecord(
        user_id=str(row.user_id),
        connector_name=row.connector_name,
        status=row.status,
        access_token_encrypted=row.access_token_encrypted,
        refresh_token_encrypted=row.refresh_token_encrypted,
        expires_at=row.expires_at,
        selected_tools=[item for item in selected if isinstance(item, str)],
    )
