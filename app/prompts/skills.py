"""已安装技能 / 插件的说明。只作为提示词，不单独注册工具。"""

from __future__ import annotations

SKILL_INSTRUCTIONS: dict[str, str] = {
    "research": "深度研究：先检索再归纳，回答里标出出处。不要编造来源。",
    "polish": "文案润色：保留原意，统一语气，直接给出改写结果。",
    "minutes": "会议纪要：整理成结论、负责人和待办。缺少的信息标成待确认。",
    "weekly": "周报生成：按本周事项写成可直接发送的周报，区分完成、进行中和阻塞。",
    "pdf": "PDF 文档处理：围绕 PDF 解析、摘要、提取与改写提供帮助；需要文件内容时先确认用户已提供或可读取的材料。",
    "excel": "Excel 电子表格处理：协助表格整理、公式、透视与数据分析；输出清晰的行列说明或可粘贴的表格结果。",
    "word": "Word 文档处理：协助公文/报告的起草、润色、结构调整与格式建议，直接给出可用正文。",
    "notion": "Notion 工作区工具：围绕页面、数据库与协作内容组织信息；若已接通 Notion 工具则优先调用，否则给出可执行的整理方案。",
    "wecom": "企业微信协作助手：协助消息、通知、群协作与工作流文案；输出适合企微场景的简洁表达。",
    "feishu": "飞书办公协作：协助文档、多维表格、会议纪要与协作沟通；输出适合飞书场景的结构化内容。",
    "image": "图像生成：根据文字描述构思高质量画面；给出清晰构图、风格与细节提示，必要时拆成可直接用于生图的提示词。",
}


def skill_notes(names: list[str], *, execute: bool) -> str:
    lines = [SKILL_INSTRUCTIONS[name] for name in names if name in SKILL_INSTRUCTIONS]
    if not lines:
        return ""
    if execute:
        head = "已启用插件/技能。按这些做法完成任务："
    else:
        head = "这些插件/技能只作为计划参考，不要执行其中的动作："
    return head + "\n" + "\n".join(f"- {line}" for line in lines)
