"""stdio 工具的用户授权。进程可以共享，授权不能共享。"""

from __future__ import annotations

from contextvars import ContextVar

from app.tools.registry import ToolRegistry, ToolSpec

CURRENT_MCP_USER: ContextVar[str | None] = ContextVar("mcp_stdio_user", default=None)


class StdioAccess:
    def __init__(self) -> None:
        self._catalog: set[str] = set()
        self._grants: dict[str, set[str]] = {}

    def set_catalog(self, names: set[str]) -> None:
        self._catalog = set(names)
        self._grants.clear()

    def update_catalog(self, names: set[str]) -> None:
        """工具列表变化时收紧目录。不新增授权，也不清空其他用户的记录。"""
        self._catalog = set(names)
        for user, granted in self._grants.items():
            self._grants[user] = {item for item in granted if item in self._catalog}

    def ensure(self, user_id: str) -> None:
        """每个用户一份目录副本。已有显式授权时不覆盖。"""
        if user_id not in self._grants:
            self._grants[user_id] = set(self._catalog)

    def grant(self, user_id: str, names: set[str]) -> None:
        if self._catalog:
            self._grants[user_id] = {item for item in names if item in self._catalog}
        else:
            self._grants[user_id] = set(names)

    def revoke(self, user_id: str) -> None:
        self._grants[user_id] = set()

    def allows(self, user_id: str | None, tool_name: str) -> bool:
        if not user_id or tool_name not in self._catalog:
            return False
        return tool_name in self._grants.get(user_id, set())

    def catalog(self) -> set[str]:
        return set(self._catalog)

    def has_user(self, user_id: str) -> bool:
        return user_id in self._grants

    def names_for(self, user_id: str | None) -> set[str]:
        if not user_id:
            return set()
        return set(self._grants.get(user_id, set()))

    def specs(self, registry: ToolRegistry, user_id: str | None) -> list[ToolSpec]:
        from app.tools.mcp_client import mcp_is_available

        if not user_id or not mcp_is_available():
            return []
        found: list[ToolSpec] = []
        for name in sorted(self.names_for(user_id)):
            spec = registry.get(name)
            if spec is not None and spec.toolset == "mcp":
                found.append(spec)
        return found


stdio_access = StdioAccess()
