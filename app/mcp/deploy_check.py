"""部署前检查。连接失败只返回原因，不返回地址、账号或密钥。"""

from __future__ import annotations

import socket

from app.core.config import Settings

REQUIRED_TABLES = (
    "mcp_connector_grants",
    "mcp_tool_grants",
    "mcp_authorization_audits",
)
MIGRATION_REVISION = "c4a8e2b71f03"
PREVIOUS_REVISION = "b7e1c0a94d11"
LOAD_TIERS = (
    {"concurrency": 10, "minutes": 10},
    {"concurrency": 30, "minutes": 30},
    {"concurrency": 50, "minutes": 30},
)


def postgres_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def database_probe(settings: Settings) -> dict[str, object]:
    """不执行迁移，也不读取业务数据。"""
    reachable = postgres_port_open(settings.postgres_host, settings.postgres_port)
    return {
        "reachable": reachable,
        "startup_required": settings.database_startup_required,
        "revision_expected": MIGRATION_REVISION,
        "previous_revision": PREVIOUS_REVISION,
        "tables": list(REQUIRED_TABLES),
        "status": "unknown" if reachable else "unavailable",
    }
