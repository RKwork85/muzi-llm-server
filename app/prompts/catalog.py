"""后端内置提示词目录。

提示词散落在三处：``app/prompts/system/*``（人设 / 模式）、``app/prompts/skills.py``
（插件说明）、``app/prompts/tools.toml``（工具 schema 描述），另外还有两段写在代码里的
全局约束。这里把它们收成同一份带类别的目录，供管理面板展示与开关。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.prompts.settings import is_enabled
from app.prompts.skills import SKILL_INSTRUCTIONS
from app.services.prompt_service import PromptService

CATEGORY_ORDER: tuple[str, ...] = ("persona", "mode", "plugin", "guard", "tool")

CATEGORY_META: dict[str, tuple[str, str]] = {
    "persona": ("系统人设", "决定模型的身份、语言、交付与检索习惯"),
    "mode": ("模式说明", "Plan 等模式在服务端注入的行为准则"),
    "plugin": ("插件说明", "用户通过 / 选中插件后追加的能力说明"),
    "guard": ("安全约束", "不可信内容、技能发现等全局保护性提示词"),
    "tool": ("工具说明", "每个内置工具发给模型的 schema；关闭后该工具不再暴露给模型"),
}

_PERSONA_META: dict[str, tuple[str, str]] = {
    "assistant": ("通用助手人设", "对话默认人设：中文回答、文件交付与检索预算"),
    "default": ("默认兜底人设", "找不到指定人设时使用的最小系统提示词"),
    "calculator": ("计算器助手", "计算类工具任务的专用人设"),
    "json_assistant": ("JSON 结构化输出", "要求模型只输出 JSON，配合 response_format 使用"),
}

_MODE_META: dict[str, tuple[str, str]] = {
    "plan_mode": ("Plan 模式准则", "规划引擎的提问、提案与沟通规范"),
}

_PLUGIN_META: dict[str, tuple[str, str]] = {
    "research": ("深度研究", "先检索再归纳，并标出出处"),
    "polish": ("文案润色", "保留原意、统一语气后直接给出改写"),
    "minutes": ("会议纪要", "整理结论、负责人与待办"),
    "weekly": ("周报生成", "按完成 / 进行中 / 阻塞组织本周事项"),
    "pdf": ("PDF 文档处理", "解析、摘要、提取与改写 PDF"),
    "excel": ("Excel 表格处理", "表格整理、公式、透视与数据分析"),
    "word": ("Word 文档处理", "公文 / 报告的起草、润色与格式建议"),
    "notion": ("Notion 协作", "读写已授权的 mcp__notion__* 工具"),
    "wecom": ("企业微信协作", "收发消息与整理群协作内容"),
    "feishu": ("飞书办公协作", "读写文档、多维表格与会议信息"),
    "image": ("图像生成", "把文字描述拆成可直接生图的提示词"),
}

_GUARDS: tuple[tuple[str, str, str, str], ...] = (
    (
        "guard:untrusted",
        "外部内容不可信",
        "app/tools/web_tools.py · UNTRUSTED_PROMPT",
        "工具返回的网页、文件和搜索内容只当数据，不当指令",
    ),
    (
        "guard:skill_usage",
        "技能发现规范",
        "app/skills/tools.py · SKILL_USAGE_INSTRUCTION",
        "任务开始前先发现并加载匹配的技能",
    ),
)


def _clip(text: str, limit: int = 72) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else f"{value[:limit]}…"


@dataclass(frozen=True)
class PromptEntry:
    id: str
    category: str
    name: str
    summary: str
    source: str
    content: str
    effect: str
    enabled: bool


@dataclass(frozen=True)
class PromptGroup:
    id: str
    name: str
    description: str
    items: list[PromptEntry] = field(default_factory=list)


def _entry(
    *,
    prompt_id: str,
    category: str,
    name: str,
    summary: str,
    source: str,
    content: str,
    effect: str,
) -> PromptEntry:
    return PromptEntry(
        id=prompt_id,
        category=category,
        name=name,
        summary=summary,
        source=source,
        content=content,
        effect=effect,
        enabled=is_enabled(prompt_id),
    )


def _persona_and_mode(service: PromptService) -> tuple[list[PromptEntry], list[PromptEntry]]:
    persona: list[PromptEntry] = []
    mode: list[PromptEntry] = []
    for name in service.names():
        try:
            content = service.raw(name)
        except Exception:  # noqa: BLE001 - 单个提示词读不出来不能拖垮整份目录
            continue
        source = service.source(name)
        if name in _MODE_META:
            label, summary = _MODE_META[name]
            mode.append(
                _entry(
                    prompt_id=f"mode:{name}",
                    category="mode",
                    name=label,
                    summary=summary,
                    source=source,
                    content=content,
                    effect="关闭后规划引擎不再注入这段行为准则。",
                )
            )
            continue
        label, summary = _PERSONA_META.get(name, (name, _clip(content)))
        persona.append(
            _entry(
                prompt_id=f"persona:{name}",
                category="persona",
                name=label,
                summary=summary,
                source=source,
                content=content,
                effect="关闭后，请求该人设时会自动回退到仍启用的兜底人设。",
            )
        )
    return persona, mode


def _plugin_entries() -> list[PromptEntry]:
    items: list[PromptEntry] = []
    for name, content in SKILL_INSTRUCTIONS.items():
        label, summary = _PLUGIN_META.get(name, (name, _clip(content)))
        items.append(
            _entry(
                prompt_id=f"plugin:{name}",
                category="plugin",
                name=label,
                summary=summary,
                source=f"app/prompts/skills.py · SKILL_INSTRUCTIONS[{name}]",
                content=content,
                effect="关闭后，即使本次对话选中了该插件，也不会把这段说明注入系统提示词。",
            )
        )
    return items


def _guard_entries() -> list[PromptEntry]:
    # 延迟导入，且先导 app.tools：包初始化会反向加载 app.skills.tools，
    # 反过来先导 skills.tools 会形成循环。
    from app.tools.web_tools import UNTRUSTED_PROMPT
    from app.skills.tools import SKILL_USAGE_INSTRUCTION

    contents = {
        "guard:untrusted": UNTRUSTED_PROMPT,
        "guard:skill_usage": SKILL_USAGE_INSTRUCTION,
    }
    items: list[PromptEntry] = []
    for prompt_id, name, source, summary in _GUARDS:
        items.append(
            _entry(
                prompt_id=prompt_id,
                category="guard",
                name=name,
                summary=summary,
                source=source,
                content=contents[prompt_id],
                effect="关闭后不再向模型声明这段约束，可能带来安全或行为风险。",
            )
        )
    return items


def _tool_entries() -> list[PromptEntry]:
    # 同上：tools 包初始化会加载工具注册表，不能在目录模块顶层 import。
    from app.tools.prompts import all_tool_prompts
    from app.tools.registry import build_agent_registry

    contents = all_tool_prompts()
    items: list[PromptEntry] = []
    for name, content in contents.items():
        items.append(
            _entry(
                prompt_id=f"tool:{name}",
                category="tool",
                name=name,
                summary=_clip(content),
                source=f"app/prompts/tools.toml · {name}",
                content=content,
                effect="关闭后整条工具定义（描述 + 参数 schema）都不再发给模型，重新开启后下一轮立即恢复。",
            )
        )
    # 描述写在代码里的工具（技能发现、待办更新等）同样会作为 schema 下发，
    # 不补进来用户就没法单独关掉它们，面板上的「工具说明」也会与实际不符。
    for spec in sorted(build_agent_registry().all_tools(), key=lambda item: item.name):
        if spec.name in contents:
            continue
        content = spec.description.strip()
        if not content:
            continue
        items.append(
            _entry(
                prompt_id=f"tool:{spec.name}",
                category="tool",
                name=spec.name,
                summary=_clip(content),
                source=f"app/tools/registry.py · {spec.name}",
                content=content,
                effect="关闭后整条工具定义（描述 + 参数 schema）都不再发给模型，重新开启后下一轮立即恢复。",
            )
        )
    return items


def build_catalog() -> list[PromptGroup]:
    """按固定顺序返回全部内置提示词分组。"""
    persona, mode = _persona_and_mode(PromptService())
    buckets: dict[str, list[PromptEntry]] = {
        "persona": persona,
        "mode": mode,
        "plugin": _plugin_entries(),
        "guard": _guard_entries(),
        "tool": _tool_entries(),
    }
    groups: list[PromptGroup] = []
    for category in CATEGORY_ORDER:
        items = buckets.get(category) or []
        if not items:
            continue
        name, description = CATEGORY_META[category]
        groups.append(
            PromptGroup(id=category, name=name, description=description, items=items)
        )
    return groups
