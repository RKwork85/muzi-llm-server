"""已安装技能的说明。只作为提示词，不单独注册工具。"""

from __future__ import annotations

SKILL_INSTRUCTIONS: dict[str, str] = {
    "research": "深度研究：先检索再归纳，回答里标出出处。不要编造来源。",
    "polish": "文案润色：保留原意，统一语气，直接给出改写结果。",
    "minutes": "会议纪要：整理成结论、负责人和待办。缺少的信息标成待确认。",
    "weekly": "周报生成：按本周事项写成可直接发送的周报，区分完成、进行中和阻塞。",
}


def skill_notes(names: list[str], *, execute: bool) -> str:
    lines = [SKILL_INSTRUCTIONS[name] for name in names if name in SKILL_INSTRUCTIONS]
    if not lines:
        return ""
    if execute:
        head = "已启用技能。按这些做法完成任务："
    else:
        head = "这些技能只作为计划参考，不要执行其中的动作："
    return head + "\n" + "\n".join(f"- {line}" for line in lines)
