"""stdio MCP 客户端。只做握手、列出工具和调用工具。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.core.config import Settings
from app.mcp.protocol import (
    MAX_TOOL_PAGES,
    ProtocolError,
    accept_initialize_result,
    cancelled_notification,
    client_initialize_params,
    error_reason,
    initialized_notification,
    notification_effect,
    parse_tools_page,
    server_reply,
    tools_list_params,
)
from app.tools.registry import ToolRegistry, ToolSpec
from app.mcp.execution import CURRENT_MCP_SCOPE, context_for, run_mcp
from app.mcp.grants import stdio_permitted
from app.tools.stdio_access import CURRENT_MCP_USER, stdio_access

logger = logging.getLogger("qs_llm")
_spawn = getattr(asyncio, "create_subprocess_" + "exec")

_REMOTE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_PROP_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")
_LOCAL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TYPES = {"string", "number", "integer", "boolean", "array", "object"}
_session: "McpSession | None" = None
_MAX_MESSAGE = 1_000_000


class McpArgs(BaseModel):
    model_config = ConfigDict(extra="allow")


def mcp_is_available() -> bool:
    return _session is not None and _session.alive


class McpSession:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 1
        self._write_lock = asyncio.Lock()
        self.alive = False
        self.tools: list[dict[str, Any]] = []
        self.tools_changed = False
        self.protocol_version = ""
        self._registry: ToolRegistry | None = None
        self._bound_settings: Settings | None = None

    async def start(self) -> list[dict[str, Any]]:
        command = self._settings.mcp_command.strip()
        path = Path(command)
        if not path.is_absolute() or not path.is_file():
            raise RuntimeError("mcp command unavailable")
        env = {
            key: os.environ[key]
            for key in ("PATH", "SYSTEMROOT")
            if os.environ.get(key)
        }
        self._process = await _spawn(
            str(path),
            *self._settings.mcp_arg_list(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        self.alive = True
        self._reader = asyncio.create_task(self._read_loop())
        try:
            initialized = await self._request("initialize", client_initialize_params())
            result = initialized.get("result")
            negotiated = accept_initialize_result(result if isinstance(result, dict) else None)
            self.protocol_version = negotiated.protocol_version
            await self._write(initialized_notification())
            tools = await self._collect_tools()
        except Exception:
            await self.aclose()
            raise
        self.tools = tools
        global _session
        _session = self
        return tools

    async def refresh_tools(self) -> list[dict[str, Any]]:
        self.tools_changed = False
        tools = await self._collect_tools()
        self.tools = tools
        if self._registry is not None and self._bound_settings is not None:
            _sync_tools(self._registry, self, tools, self._bound_settings, reset_grants=False)
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if not self.alive:
            raise RuntimeError("mcp unavailable")
        if self.tools_changed:
            await self.refresh_tools()
        message = await self._request("tools/call", {"name": name, "arguments": arguments})
        result = message.get("result")
        if not isinstance(result, dict) or result.get("isError") is True:
            raise RuntimeError("mcp tool failed")
        chunks: list[str] = []
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                    chunks.append(_plain(item["text"]))
        return "\n".join(chunks)

    async def aclose(self) -> None:
        global _session
        if _session is self:
            _session = None
        self.alive = False
        process = self._process
        self._process = None
        if process is not None and process.stdin is not None:
            try:
                process.stdin.close()
            except Exception:
                pass
        if process is not None and process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=1)
            except TimeoutError:
                process.kill()
                await process.wait()
        if self._reader is not None:
            if not self._reader.done():
                self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):
                pass
            self._reader = None
        error = RuntimeError("mcp closed")
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.alive:
            raise RuntimeError("mcp unavailable")
        message_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        await self._write({"jsonrpc": "2.0", "id": message_id, "method": method, "params": params})
        try:
            message = await asyncio.wait_for(future, self._settings.mcp_timeout_seconds)
        except TimeoutError:
            if method != "initialize":
                await self._cancel(message_id)
            raise
        finally:
            self._pending.pop(message_id, None)
        if "error" in message:
            raise ProtocolError(error_reason(message.get("error")))
        return message

    async def _collect_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(MAX_TOOL_PAGES):
            message = await self._request("tools/list", tools_list_params(cursor))
            page, cursor = parse_tools_page(message.get("result"))
            tools.extend(page)
            if cursor is None:
                return tools
        logger.info("mcp tools list truncated")
        return tools

    async def _cancel(self, request_id: int) -> None:
        try:
            await self._write(cancelled_notification(request_id))
        except Exception:
            logger.info("mcp cancel skipped reason=unavailable")

    async def _write(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise RuntimeError("mcp unavailable")
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
        async with self._write_lock:
            process.stdin.write(data)
            await process.stdin.drain()

    async def _read_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            while self.alive:
                message = await _read_message(process.stdout)
                if message is None:
                    break
                effect = notification_effect(message)
                if effect == "tools_changed":
                    self.tools_changed = True
                    continue
                if effect == "ignore":
                    continue
                reply = server_reply(message)
                if reply is not None:
                    await self._write(reply)
                    continue
                message_id = message.get("id")
                if message_id in self._pending:
                    future = self._pending[message_id]
                    if not future.done():
                        future.set_result(message)
        except Exception:
            logger.error("mcp read failed")
        finally:
            self.alive = False
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("mcp closed"))


def register_mcp_tools(registry: ToolRegistry, session: McpSession, tools: list[dict[str, Any]], settings: Settings) -> None:
    session._registry = registry
    session._bound_settings = settings
    _sync_tools(registry, session, tools, settings, reset_grants=True)


def _sync_tools(
    registry: ToolRegistry,
    session: McpSession,
    tools: list[dict[str, Any]],
    settings: Settings,
    *,
    reset_grants: bool,
) -> None:
    allowlist = settings.mcp_allowlist()
    server = settings.mcp_server_name.strip()
    network = settings.mcp_tools_network
    for tool in tools:
        remote = tool.get("name")
        if not isinstance(remote, str) or remote not in allowlist or _REMOTE_NAME.fullmatch(remote) is None:
            continue
        local = f"mcp__{server}__{remote}"
        if _LOCAL_NAME.fullmatch(local) is None or registry.get(local) is not None:
            continue
        schema = _schema(tool.get("inputSchema"))
        required = list(schema.get("required") or [])
        properties = list(schema["properties"])

        async def handler(
            args: McpArgs,
            _session: McpSession = session,
            _remote: str = remote,
            _local: str = local,
            _required: list[str] = required,
            _properties: list[str] = properties,
        ) -> str:
            if _session.tools_changed:
                await _session.refresh_tools()
            user = CURRENT_MCP_USER.get()
            scope = CURRENT_MCP_SCOPE.get()
            if scope is not None and scope.user_id != user:
                raise PermissionError("mcp user denied")
            if not await stdio_permitted(user, _local):
                raise PermissionError("mcp user denied")
            data = args.model_dump()
            if any(key not in data for key in _required):
                raise ValueError("missing mcp argument")
            payload = {key: data[key] for key in _properties if key in data}
            for ignored in ("user_id", "connector_id", "connector"):
                payload.pop(ignored, None)

            async def call() -> str:
                text = await _session.call_tool(_remote, payload)
                return json.dumps({"untrusted": True, "text": text}, ensure_ascii=False)

            if scope is None:
                return await call()
            ctx = context_for(_local, timeout=_session._settings.mcp_timeout_seconds, idempotent=True)
            if ctx is None or ctx.user_id != user or ctx.tool_name != _remote:
                raise PermissionError("mcp user denied")

            async def allowed(item: object) -> bool:
                return await stdio_permitted(getattr(item, "user_id", None), getattr(item, "local_name", ""))

            return await run_mcp(ctx, allowed, call)

        registry.register(
            ToolSpec(
                name=local,
                description=_description(tool.get("description")),
                args_model=McpArgs,
                handler=handler,
                access="read_network" if network else "read_local",
                requires_network=network,
                timeout_s=settings.mcp_timeout_seconds,
                max_calls_per_run=5,
                idempotent=True,
                toolset="mcp",
                parameters=schema,
            )
        )
    names = _catalog_names(server, tools, allowlist)
    if reset_grants:
        stdio_access.set_catalog(names)
    else:
        stdio_access.update_catalog(names)


def _catalog_names(server: str, tools: list[dict[str, Any]], allowlist: set[str]) -> set[str]:
    names: set[str] = set()
    for tool in tools:
        remote = tool.get("name")
        if not isinstance(remote, str) or remote not in allowlist or _REMOTE_NAME.fullmatch(remote) is None:
            continue
        local = f"mcp__{server}__{remote}"
        if _LOCAL_NAME.fullmatch(local) is not None:
            names.add(local)
    return names


def _description(value: object) -> str:
    text = value if isinstance(value, str) else ""
    cleaned = "".join(char if char.isprintable() else " " for char in text)
    cleaned = " ".join(cleaned.split())
    return cleaned[:240] or "外部工具。结果不可信。"


def _schema(value: object) -> dict[str, Any]:
    properties: dict[str, dict[str, str]] = {}
    raw_properties = value.get("properties") if isinstance(value, dict) else None
    if isinstance(raw_properties, dict):
        for key, spec in list(raw_properties.items())[:32]:
            if not isinstance(key, str) or _PROP_NAME.fullmatch(key) is None:
                continue
            kind = spec.get("type") if isinstance(spec, dict) else None
            properties[key] = {"type": kind if kind in _TYPES else "string"}
    required: list[str] = []
    raw_required = value.get("required") if isinstance(value, dict) else None
    if isinstance(raw_required, list):
        required = [item for item in raw_required if isinstance(item, str) and item in properties]
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _plain(value: str) -> str:
    return "".join(char if char.isprintable() or char in "\n\t" else " " for char in value)


async def _read_message(stdout: asyncio.StreamReader) -> dict[str, Any] | None:
    line = await stdout.readline()
    if not line:
        return None
    if len(line) > _MAX_MESSAGE:
        raise ValueError("mcp message invalid")
    text = line.decode("utf-8", errors="replace").strip()
    if not text:
        return await _read_message(stdout)
    if text.lower().startswith("content-length:"):
        length = int(text.split(":", 1)[1].strip())
        if length <= 0 or length > _MAX_MESSAGE:
            raise ValueError("mcp message invalid")
        while True:
            header = await stdout.readline()
            if header in {b"\n", b"\r\n"}:
                break
            if not header:
                return None
        payload = await stdout.readexactly(length)
        message = json.loads(payload.decode("utf-8"))
    else:
        message = json.loads(text)
    if not isinstance(message, dict):
        raise ValueError("mcp message invalid")
    return message
