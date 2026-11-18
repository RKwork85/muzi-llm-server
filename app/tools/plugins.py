"""输入框 `/` 插件面板的能力投影。

插件不是工具：它只是注入一段能力说明（`app/prompts/skills.py`）。但它会向模型
承诺「你可以做这件事」，所以必须和工具名单一样经过同一套投影，否则就会出现
「面板说能处理 PDF，模型手里却没有读文件的工具」。

判定规则（顺序即拒绝原因的优先级）：

1. 功能权限 `plugin.access`——没有它，整个插件面板为空。
2. 模式：插件声明的 `modes`，不给就是全模式可见。
3. 连接器授权 `connector`：协作类插件只能用**用户自己授权过**的远端连接器工具，
   没授权就等于没有工具，挂出来只会让模型以为它能读 Notion。
4. 工具依赖 `requires_any`：至少有一个出现在当前模式的可见名单里。
   空元组表示纯提示词插件（不依赖任何工具）。

被拒的插件带原因返回，前端可以据此置灰或隐藏，不需要自己猜。
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass

from app.tools.capabilities import MCP_MODES

PLUGIN_ACCESS = "plugin.access"
CONNECTOR_PREFIX = "mcp__"


@dataclass(frozen=True)
class PluginSpec:
    """一个 `/` 面板条目。id 必须与前端 `SLASH_PLUGINS` 的 id 一致。"""

    id: str
    requires_any: tuple[str, ...] = ()
    # 需要用户在该连接器上完成按人授权（OAuth 或 bridge 凭据），否则不成立。
    connector: str | None = None
    # None 表示全模式可见；连接器类插件只可能出现在真正注入 MCP 的模式里。
    modes: frozenset[str] | None = None


# 与前端 `src/modules/conversation/constants/slashPlugins.js` 一一对应。
# 调整依赖时只改这里：前端不再维护「哪些插件能用」的判断。
PLUGIN_SPECS: tuple[PluginSpec, ...] = (
    # 处理 PDF/表格至少要能读工作区里的文件。
    PluginSpec("pdf", requires_any=("read_text_file", "list_directory")),
    PluginSpec("excel", requires_any=("read_text_file", "list_directory")),
    # 写 Word 需要真正的文档写入工具（deliver_file 只负责外发，不负责生成）。
    PluginSpec("word", requires_any=("write_docx", "write_text_file")),
    # 协作类插件没有内置工具：能力全部来自用户授权的远端连接器。
    # 没授权就是「面板里有、模型手里没有」，所以必须和连接器授权绑定。
    PluginSpec("notion", connector="notion", modes=MCP_MODES),
    PluginSpec("wecom", connector="wecom", modes=MCP_MODES),
    PluginSpec("feishu", connector="feishu", modes=MCP_MODES),
    # 图像生成当前由图片模型通道完成，插件只提供提示词建议。
    PluginSpec("image"),
)

PLUGIN_BY_ID: dict[str, PluginSpec] = {spec.id: spec for spec in PLUGIN_SPECS}


@dataclass(frozen=True)
class PluginVisibility:
    allowed: tuple[str, ...]
    denied: dict[str, str]

    def __contains__(self, plugin_id: object) -> bool:
        return plugin_id in self.allowed


def connector_names_from_tools(tools: Iterable[str]) -> frozenset[str]:
    """从工具名反推「本次真的挂上了哪些连接器」。

    `mcp__<连接器>__<工具名>` 是本仓库唯一的远端工具命名规则（见
    `app/mcp/service.py` 的 `_spec`）。用它当事实源，比另建一张表更不容易漂移。
    """
    found: set[str] = set()
    for name in tools:
        if not name.startswith(CONNECTOR_PREFIX):
            continue
        rest = name[len(CONNECTOR_PREFIX) :]
        connector, _, remote = rest.partition("__")
        if connector and remote:
            found.add(connector)
    return frozenset(found)


def visible_plugins(
    visible_tools: Collection[str],
    *,
    permissions: Collection[str] | None,
    mode: str = "ask",
    connectors: Collection[str] | None = None,
) -> PluginVisibility:
    """按当前模式的工具名单、连接器授权与功能权限投影出插件面板。

    `permissions=None` 表示没有授权上下文（静态目录/测试）：只按模式与工具依赖
    判断，不推断 `plugin.access`，与 `visible_tools` 的口径保持一致。

    `connectors=None` 表示「不知道用户授权了哪些连接器」，按未授权处理：
    面板不承诺一件我们没法确认的事。
    """
    tools = frozenset(visible_tools)
    authorized = frozenset(connectors or ())
    allowed: list[str] = []
    denied: dict[str, str] = {}

    if permissions is not None and PLUGIN_ACCESS not in frozenset(permissions):
        return PluginVisibility((), {spec.id: "permission_missing" for spec in PLUGIN_SPECS})

    for spec in PLUGIN_SPECS:
        if spec.modes is not None and mode not in spec.modes:
            denied[spec.id] = "mode_not_offered"
            continue
        if spec.connector is not None and spec.connector not in authorized:
            denied[spec.id] = "connector_unauthorized"
            continue
        if spec.requires_any and not tools.intersection(spec.requires_any):
            denied[spec.id] = "capability_missing"
            continue
        allowed.append(spec.id)

    return PluginVisibility(tuple(allowed), denied)


def allowed_plugin_names(names: Iterable[str], visible: PluginVisibility) -> tuple[list[str], list[str]]:
    """把请求里的 `skill_names` 拆成「可用」与「被拒」两组。

    只有 `/` 面板里的条目（`PLUGIN_SPECS`）参与能力判定：面板插件当前不可用时
    直接拒绝。其余名字是历史备注键（research/polish/...），没有工具承诺，交给
    `skill_notes` 按白名单过滤，保持旧接口兼容。
    """
    kept: list[str] = []
    rejected: list[str] = []
    for name in names:
        if name in visible or name not in PLUGIN_BY_ID:
            kept.append(name)
        else:
            rejected.append(name)
    return kept, rejected
