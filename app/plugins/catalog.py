"""静态插件目录。MCP 连接器（Notion/企业微信/飞书）不在本目录，见 app/mcp/connectors.toml。"""

from __future__ import annotations

from dataclasses import dataclass

PLUGIN_CATEGORIES = ("金融", "效率与办公", "创意与设计", "开发与工程", "学习与研究", "法律")


@dataclass(frozen=True)
class Plugin:
    id: str
    name: str
    desc: str
    categories: tuple[str, ...]
    featured: bool = False
    default_installed: bool = False
    bg: str = "#4f46e5"
    fg: str = "#fff"
    mark: str = ""


_PLUGINS: tuple[Plugin, ...] = tuple(
    Plugin(**item)  # type: ignore[arg-type]
    for item in (
        {
            "id": "pdf",
            "name": "PDF",
            "desc": "PDF 文档处理",
            "categories": ("效率与办公",),
            "default_installed": True,
            "bg": "#f15b50",
            "fg": "#fff",
            "mark": "PDF",
        },
        {
            "id": "excel",
            "name": "Excel",
            "desc": "Excel 电子表格处理工具",
            "categories": ("效率与办公",),
            "default_installed": True,
            "bg": "#21a366",
            "fg": "#fff",
            "mark": "X",
        },
        {
            "id": "word",
            "name": "Word",
            "desc": "Word 文档处理工具",
            "categories": ("效率与办公",),
            "default_installed": True,
            "bg": "#2b579a",
            "fg": "#fff",
            "mark": "W",
        },
        {
            "id": "image",
            "name": "图像生成",
            "desc": "根据文字描述生成高质量图像",
            "categories": ("创意与设计",),
            "default_installed": True,
            "bg": "#f59a3a",
            "fg": "#fff",
            "mark": "图",
        },
        {
            "id": "find",
            "name": "同花顺FinD金融数据",
            "desc": "同花顺FinD金融数据",
            "categories": ("金融",),
            "featured": True,
            "bg": "#e23d3d",
            "fg": "#fff",
            "mark": "同",
        },
        {
            "id": "tianyancha",
            "name": "天眼查",
            "desc": "企业信息查询",
            "categories": ("金融",),
            "featured": True,
            "bg": "#2f6bff",
            "fg": "#fff",
            "mark": "眼",
        },
        {
            "id": "hualv",
            "name": "华律元典",
            "desc": "中国法律法规、案例数据库",
            "categories": ("法律",),
            "featured": True,
            "bg": "#3b6fd8",
            "fg": "#fff",
            "mark": "律",
        },
        {
            "id": "academic",
            "name": "学术数据库",
            "desc": "学术文献与科研资料检索",
            "categories": ("学习与研究",),
            "featured": True,
            "bg": "#3d7ee8",
            "fg": "#fff",
            "mark": "学",
        },
        {
            "id": "markets",
            "name": "全球金融数据",
            "desc": "查询全球主要市场金融数据",
            "categories": ("金融",),
            "bg": "#6d5efc",
            "fg": "#fff",
            "mark": "金",
        },
        {
            "id": "worldbank",
            "name": "世界银行",
            "desc": "查看世界银行公开数据",
            "categories": ("金融", "学习与研究"),
            "bg": "#14956a",
            "fg": "#fff",
            "mark": "世",
        },
        {
            "id": "imf",
            "name": "IMF",
            "desc": "国际货币基金组织数据库",
            "categories": ("金融",),
            "bg": "#0e8a7d",
            "fg": "#fff",
            "mark": "IMF",
        },
        {
            "id": "oecd",
            "name": "OECD",
            "desc": "经济合作与发展组织数据库",
            "categories": ("金融", "学习与研究"),
            "bg": "#3a7bd5",
            "fg": "#fff",
            "mark": "经",
        },
        {
            "id": "stats",
            "name": "国家统计局",
            "desc": "中国官方统计数据",
            "categories": ("学习与研究",),
            "bg": "#5b6ee1",
            "fg": "#fff",
            "mark": "统",
        },
        {
            "id": "github",
            "name": "GitHub",
            "desc": "查看仓库、议题和拉取请求",
            "categories": ("开发与工程",),
            "bg": "#24292f",
            "fg": "#fff",
            "mark": "GH",
        },
        {
            "id": "gmail",
            "name": "Gmail",
            "desc": "Gmail 邮件与沟通管理工具",
            "categories": ("效率与办公",),
            "featured": True,
            "bg": "#ffffff",
            "fg": "#ea4335",
            "mark": "M",
        },
        {
            "id": "calendar",
            "name": "Google Calendar",
            "desc": "Google Calendar 管理工具",
            "categories": ("效率与办公",),
            "featured": True,
            "bg": "#ffffff",
            "fg": "#1a73e8",
            "mark": "31",
        },
        {
            "id": "drive",
            "name": "Google Drive",
            "desc": "Google Drive管理工具",
            "categories": ("效率与办公",),
            "featured": True,
            "bg": "#ffffff",
            "fg": "#188038",
            "mark": "D",
        },
        {
            "id": "outlook",
            "name": "Outlook",
            "desc": "连接 Outlook 邮件与日历",
            "categories": ("效率与办公",),
            "featured": True,
            "bg": "#0f6cbd",
            "fg": "#fff",
            "mark": "O",
        },
    )
)


def all_plugins() -> tuple[Plugin, ...]:
    return _PLUGINS


def plugin_ids() -> frozenset[str]:
    return frozenset(item.id for item in _PLUGINS)
