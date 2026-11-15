"""静态插件目录与安装状态逻辑测试。不连接数据库。"""

from __future__ import annotations

from app.api.v1.endpoints.plugins import compute_plugins
from app.plugins.catalog import PLUGIN_CATEGORIES, all_plugins, plugin_ids


def test_catalog_defaults_and_excludes_mcp() -> None:
    plugins = {item.id: item for item in all_plugins()}
    # 默认安装的静态插件只有 PDF/Excel/Word/图像生成
    assert {item.id for item in all_plugins() if item.default_installed} == {"pdf", "excel", "word", "image"}
    # MCP 连接器（Notion/企业微信/飞书）不在静态目录
    assert not {"notion", "wecom", "feishu"} & set(plugins)
    assert "find" in plugins and "gmail" in plugins


def test_plugin_ids_are_stable() -> None:
    ids = plugin_ids()
    assert "pdf" in ids and "image" in ids and "github" in ids


def test_compute_plugins_applies_defaults() -> None:
    plugins, installed = compute_plugins({})
    by_id = {item["id"]: item for item in plugins}
    assert by_id["pdf"]["installed"] is True
    assert by_id["find"]["installed"] is False
    assert set(installed) == {"pdf", "excel", "word", "image"}


def test_compute_plugins_explicit_state_overrides_default() -> None:
    plugins, installed = compute_plugins({"pdf": "uninstalled", "find": "installed"})
    by_id = {item["id"]: item for item in plugins}
    assert by_id["pdf"]["installed"] is False  # 显式卸载覆盖默认安装
    assert by_id["find"]["installed"] is True
    assert "pdf" not in installed
    assert "find" in installed


def test_categories_cover_all_plugins() -> None:
    for item in all_plugins():
        assert all(category in PLUGIN_CATEGORIES for category in item.categories)
