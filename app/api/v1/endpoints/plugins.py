"""静态插件目录与安装状态。MCP 连接器走 /mcp/connectors，不在此。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.api.deps import get_authenticated_user
from app.authorization.dependencies import require_permission
from app.core.database.deps import DbSession
from app.core.exceptions import InvalidRequestError
from app.models.tables import PluginInstallation, User
from app.plugins.catalog import all_plugins, plugin_ids

router = APIRouter(prefix="/plugins", tags=["plugins"])

# 安装/卸载是写操作；目录与安装状态读取不加校验。
PluginWriter = Annotated[User, Depends(require_permission("plugin.write"))]


def _entry(item) -> dict[str, object]:
    return {
        "id": item.id,
        "name": item.name,
        "desc": item.desc,
        "categories": list(item.categories),
        "featured": item.featured,
        "default_installed": item.default_installed,
        "bg": item.bg,
        "fg": item.fg,
        "mark": item.mark,
    }


def compute_plugins(states: dict[str, str]) -> tuple[list[dict[str, object]], list[str]]:
    """把目录与用户显式状态合并成展示列表。无记录时按 default_installed。"""
    plugins: list[dict[str, object]] = []
    installed: list[str] = []
    for item in all_plugins():
        state = states.get(item.id)
        is_installed = state == "installed" if state is not None else item.default_installed
        plugins.append({**_entry(item), "installed": is_installed})
        if is_installed:
            installed.append(item.id)
    return plugins, installed


@router.get("", summary="列出插件目录和当前用户安装状态")
async def list_plugins(
    user: Annotated[User, Depends(get_authenticated_user)],
    session: DbSession,
) -> dict[str, object]:
    rows = await session.scalars(select(PluginInstallation).where(PluginInstallation.user_id == user.id))
    states = {row.plugin_id: row.status for row in rows}
    plugins, installed = compute_plugins(states)
    return {"plugins": plugins, "installed": installed}


@router.post("/{plugin_id}/install", summary="安装插件")
async def install_plugin(
    plugin_id: str,
    user: PluginWriter,
    session: DbSession,
) -> dict[str, object]:
    if plugin_id not in plugin_ids():
        raise InvalidRequestError("插件不存在")
    await _set_status(session, user, plugin_id, "installed")
    return {"status": "installed"}


@router.delete("/{plugin_id}/uninstall", summary="卸载插件")
async def uninstall_plugin(
    plugin_id: str,
    user: PluginWriter,
    session: DbSession,
) -> dict[str, object]:
    if plugin_id not in plugin_ids():
        raise InvalidRequestError("插件不存在")
    await _set_status(session, user, plugin_id, "uninstalled")
    return {"status": "uninstalled"}


async def _set_status(session: DbSession, user: User, plugin_id: str, status: str) -> None:
    row = await session.scalar(
        select(PluginInstallation).where(
            PluginInstallation.user_id == user.id,
            PluginInstallation.plugin_id == plugin_id,
        )
    )
    if row is None:
        session.add(PluginInstallation(user_id=user.id, plugin_id=plugin_id, status=status))
    else:
        row.status = status
    await session.flush()
