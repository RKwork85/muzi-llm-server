"""Ask 与 Agent 的能力边界。模型不能自行扩大。"""

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

AGENT_GRANT_BY_TOOL = {
    "write_text_file": "write_file",
    "write_docx": "write_file",
    "delete_file": "delete_file",
    "run_command": "run_command",
}


# Skill 工具（发现/加载/读取资源）。Ask 模式屏蔽 Skill，只在 Plan（仅发现元数据）
# 与 Agent（完整能力）中开放。
SKILL_TOOL_NAMES = frozenset(
    {
        "discover_skills",
        "load_skill",
        "read_skill_resource",
    }
)


def supports_orchestration(mode: str) -> bool:
    """多步骤任务编排只存在于 Agent 运行，Ask 不能启动它。"""
    return mode == "agent"
