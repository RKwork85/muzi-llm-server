"""Ask / Plan / Agent 的工具白名单。

这里只回答「某个模式提供哪些工具」。是否真的可用由
`app.authorization.application.visibility` 按权限与资源作用域投影得出。
"""

from __future__ import annotations

PLAN_TOOLS = frozenset(
    {
        "calculator",
        "get_current_time",
        "web_search",
        "web_fetch",
        "weather_query",
        "list_directory",
        "read_text_file",
        "search_text",
        "create_flowchart",
        "create_sequence_diagram",
        "validate_mermaid",
        # Plan 只做渐进式加载的 Level 1：发现技能元数据用于制定计划，不加载 SKILL.md 正文、不读资源。
        "discover_skills",
    }
)

ASK_TOOLS = frozenset(
    {
        "calculator",
        "get_current_time",
        "web_search",
        "web_fetch",
        "weather_query",
        "list_directory",
        "read_text_file",
        "search_text",
        "write_text_file",
        "write_docx",
        "deliver_file",
        "create_flowchart",
        "create_sequence_diagram",
        "validate_mermaid",
        "run_python",
        "discover_skills",
        "load_skill",
        "read_skill_resource",
    }
)

# Skill 工具（发现/加载/读取资源）。Ask 模式屏蔽 Skill，只在 Plan（仅发现元数据）
# 与 Agent（完整能力）中开放。
SKILL_TOOL_NAMES = frozenset(
    {
        "discover_skills",
        "load_skill",
        "read_skill_resource",
    }
)

# 技能脚本执行只在 Agent 模式提供：它等价于在沙盒里跑任意代码。
SKILL_SCRIPT_TOOL = "execute_skill_script"

# 执行期清单回写只在 Agent 运行时可用：它是「执行计划」这一动作的进度通道，
# Ask/Plan 没有执行期，暴露它只会让模型误以为能改计划。
PLANNING_TODO_TOOL = "update_todos"

# 可以挂远端连接器（Notion / 企业微信 / 飞书）的模式。
# Agent 是完整运行环境；Ask 是对外协作的主要入口，用户会在这里直接用 Notion/飞书，
# 所以两者都注入 MCP。Plan 只做只读规划，不调用外部应用。
MCP_MODES = frozenset({"ask", "agent"})

# 模式 × 工具白名单。权限只能在白名单之内继续收窄，不能扩大。
MODE_TOOL_NAMES: dict[str, frozenset[str]] = {
    "ask": ASK_TOOLS - SKILL_TOOL_NAMES - frozenset({SKILL_SCRIPT_TOOL}),
    "plan": PLAN_TOOLS,
    "agent": ASK_TOOLS | SKILL_TOOL_NAMES | frozenset({SKILL_SCRIPT_TOOL, PLANNING_TODO_TOOL}),
}

# 显式声明 toolsets 时不提供图表工具，避免默认请求被工具集噪声放大。
BASELINE_EXCLUDED_TOOLSETS = frozenset({"diagram"})


def supports_orchestration(mode: str) -> bool:
    """多步骤任务编排只存在于 Agent 运行，Ask 不能启动它。"""
    return mode == "agent"
