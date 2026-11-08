"""远程 MCP 传输。Streamable HTTP 为主，SSE 为同一套 JSON-RPC 的另一种运载。"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import httpx

from app.mcp.protocol import (
    PREFERRED_PROTOCOL_VERSION,
    ProtocolError,
    accept_initialize_result,
    cancelled_notification,
    client_initialize_params,
    error_reason,
    initialized_notification,
    notification_effect,
    server_reply,
)

logger = logging.getLogger("qs_llm")


@dataclass
class RemoteSession:
    transport: str
    url: str
    token: str
    client: httpx.AsyncClient = field(repr=False)
    session_id: str | None = None
    protocol: str = PREFERRED_PROTOCOL_VERSION
    tools_changed: bool = False
    next_id: int = 1
    sse_lines: Any = None
    sse_response: Any = None
    message_url: str | None = None


_HUBS: dict[int, "RemoteHub"] = {}


def shared_hub(client: httpx.AsyncClient) -> "RemoteHub":
    hub = _HUBS.get(id(client))
    if hub is None:
        hub = RemoteHub(client)
        _HUBS[id(client)] = hub
    return hub


def release_hub(client: httpx.AsyncClient) -> None:
    _HUBS.pop(id(client), None)


class RemoteHub:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._sessions: dict[tuple[str, str], RemoteSession] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    def drop(self, user_id: str, name: str) -> None:
        self._sessions.pop((user_id, name), None)

    async def close_session(self, user_id: str, name: str) -> None:
        session = self._sessions.pop((user_id, name), None)
        if session is not None:
            await self._close_transport(session)

    async def call(
        self,
        user_id: str,
        name: str,
        transport: str,
        url: str,
        token: str,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        key = (user_id, name)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        async with lock:
            for attempt in (1, 2):
                try:
                    return await self._invoke(key, transport, url, token, method, params)
                except ProtocolError as exc:
                    if not exc.fatal:
                        raise
                    session = self._sessions.pop(key, None)
                    if session is not None:
                        await self._close_transport(session)
                    if exc.reason != "session_invalid" or attempt == 2:
                        raise
            raise ProtocolError("session_invalid")

    async def _invoke(
        self,
        key: tuple[str, str],
        transport: str,
        url: str,
        token: str,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        session = self._sessions.get(key)
        if session is None or session.token != token or session.url != url or session.transport != transport:
            session = RemoteSession(transport=transport, url=url, token=token, client=self._client)
            self._sessions[key] = session
            if method != "initialize":
                await self._handshake(session)
        return await self._rpc(session, method, params)

    async def _handshake(self, session: RemoteSession) -> None:
        try:
            message = await self._rpc(session, "initialize", client_initialize_params())
            result = message.get("result")
            negotiated = accept_initialize_result(result if isinstance(result, dict) else None)
        except ProtocolError as exc:
            if exc.reason == "session_invalid" or exc.fatal:
                raise
            raise ProtocolError("initialize_result") from None
        session.protocol = negotiated.protocol_version
        await self._roundtrip(session, initialized_notification(), wait=False)

    async def _rpc(self, session: RemoteSession, method: str, params: dict[str, Any]) -> dict[str, Any]:
        message_id = session.next_id
        session.next_id += 1
        payload = {"jsonrpc": "2.0", "id": message_id, "method": method, "params": params}
        try:
            message = await self._roundtrip(session, payload)
        except httpx.TimeoutException:
            if method != "initialize":
                await self._cancel(session, message_id)
            raise ProtocolError("timeout") from None
        message = await self._unwrap(session, message, message_id)
        if "error" in message:
            raise ProtocolError(error_reason(message.get("error")))
        return message

    async def _unwrap(self, session: RemoteSession, message: dict[str, Any], message_id: int) -> dict[str, Any]:
        while isinstance(message, dict) and isinstance(message.get("method"), str):
            effect = notification_effect(message)
            if effect == "tools_changed":
                session.tools_changed = True
            elif effect != "ignore":
                reply = server_reply(message)
                if reply is not None:
                    await self._roundtrip(session, reply, wait=False)
            message = await self._read(session)
        if not isinstance(message, dict) or message.get("id") != message_id:
            raise ProtocolError("invalid_response")
        return message

    async def _cancel(self, session: RemoteSession, request_id: int) -> None:
        try:
            await self._roundtrip(session, cancelled_notification(request_id), wait=False)
        except Exception:
            logger.info("mcp cancel skipped reason=unavailable")

    async def _close_transport(self, session: RemoteSession) -> None:
        response = session.sse_response
        session.sse_response = None
        if response is not None:
            try:
                await response.aclose()
            except Exception:
                logger.info("mcp session close failed")
        if session.transport == "sse" or not session.session_id:
            return
        try:
            await session.client.delete(session.url, headers=_headers(session))
        except Exception:
            logger.info("mcp session close failed")

    async def _roundtrip(self, session: RemoteSession, payload: dict[str, Any], *, wait: bool = True) -> dict[str, Any]:
        if session.transport == "sse":
            return await self._sse(session, payload, wait=wait)
        return await self._http(session, payload, wait=wait)

    async def _http(self, session: RemoteSession, payload: dict[str, Any], *, wait: bool) -> dict[str, Any]:
        headers = _headers(session)
        response = await session.client.post(session.url, headers=headers, json=payload)
        if response.status_code == 401:
            raise PermissionError("mcp unauthorized")
        if response.status_code == 404:
            raise ProtocolError("session_invalid")
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            session.session_id = session_id
        if not wait or response.status_code == 202 or not response.content:
            return {}
        messages = _parse_messages(response.headers.get("content-type", ""), response.text)
        chosen: dict[str, Any] | None = None
        for message in messages:
            effect = notification_effect(message)
            if effect == "tools_changed":
                session.tools_changed = True
                continue
            if effect == "ignore":
                continue
            reply = server_reply(message)
            if reply is not None:
                await self._roundtrip(session, reply, wait=False)
                continue
            if chosen is None:
                chosen = message
        if chosen is None:
            raise ProtocolError("invalid_response")
        return chosen

    async def _read(self, session: RemoteSession) -> dict[str, Any]:
        if session.transport == "sse":
            return await _read_sse_message(session)
        raise ProtocolError("invalid_response")

    async def _sse(self, session: RemoteSession, payload: dict[str, Any], *, wait: bool) -> dict[str, Any]:
        if session.message_url is None:
            request = session.client.build_request(
                "GET",
                session.url,
                headers={"Authorization": f"Bearer {session.token}", "Accept": "text/event-stream"},
            )
            response = await session.client.send(request, stream=True)
            if response.status_code == 401:
                raise PermissionError("mcp unauthorized")
            session.sse_response = response
            session.sse_lines = response.aiter_lines()
            endpoint = await _read_sse_endpoint(session)
            session.message_url = urljoin(session.url, endpoint)
        post = await session.client.post(
            session.message_url,
            headers={"Authorization": f"Bearer {session.token}", "Content-Type": "application/json"},
            json=payload,
        )
        if post.status_code == 401:
            raise PermissionError("mcp unauthorized")
        if post.status_code == 404:
            raise ProtocolError("session_invalid")
        if not wait:
            return {}
        if post.content and "application/json" in post.headers.get("content-type", ""):
            message = post.json()
            if isinstance(message, dict):
                return message
        return await _read_sse_message(session)


def _headers(session: RemoteSession) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {session.token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": session.protocol,
    }
    if session.session_id:
        headers["Mcp-Session-Id"] = session.session_id
    return headers


def _parse_messages(content_type: str, text: str) -> list[dict[str, Any]]:
    if "text/event-stream" in content_type:
        messages: list[dict[str, Any]] = []
        for chunk in text.split("\n\n"):
            data = "\n".join(line[5:].strip() for line in chunk.splitlines() if line.startswith("data:"))
            if not data:
                continue
            message = json.loads(data)
            if isinstance(message, dict):
                messages.append(message)
        if not messages:
            raise ProtocolError("invalid_response")
        return messages
    message = json.loads(text)
    if not isinstance(message, dict):
        raise ProtocolError("invalid_response")
    return [message]


async def _read_sse_endpoint(session: RemoteSession) -> str:
    event = "message"
    data: list[str] = []
    async for line in session.sse_lines:
        if line == "":
            if event == "endpoint" and data:
                return "\n".join(data).strip()
            event = "message"
            data = []
            continue
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data.append(line.split(":", 1)[1].strip())
    raise ProtocolError("invalid_response")


async def _read_sse_message(session: RemoteSession) -> dict[str, Any]:
    data: list[str] = []
    async for line in session.sse_lines:
        if line == "":
            if data:
                message = json.loads("\n".join(data))
                if isinstance(message, dict):
                    return message
            data = []
            continue
        if line.startswith("data:"):
            data.append(line.split(":", 1)[1].strip())
    raise ProtocolError("invalid_response")
