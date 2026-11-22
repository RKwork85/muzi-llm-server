"""已安装技能 / 插件的说明。只作为提示词，不单独注册工具。"""

from __future__ import annotations

from app.prompts.settings import is_enabled

SKILL_INSTRUCTIONS: dict[str, str] = {
    "research": "深度研究：先检索再归纳，回答里标出出处。不要编造来源。",
    "polish": "文案润色：保留原意，统一语气，直接给出改写结果。",
    "minutes": "会议纪要：整理成结论、负责人和待办。缺少的信息标成待确认。",
    "weekly": "周报生成：按本周事项写成可直接发送的周报，区分完成、进行中和阻塞。",
    "pdf": "PDF 文档处理：围绕 PDF 解析、摘要、提取与改写提供帮助；需要文件内容时先确认用户已提供或可读取的材料。",
    "excel": "Excel 电子表格处理：协助表格整理、公式、透视与数据分析；输出清晰的行列说明或可粘贴的表格结果。",
    "word": "Word 文档处理：协助公文/报告的起草、润色、结构调整与格式建议，直接给出可用正文。",
    # 协作类插件的工具来自用户自己授权的连接器（mcp__notion__* / mcp__wecom__* / mcp__feishu__*）。
    # 面板只在连接器真的挂上工具时才出现，所以这里不能再留"没接通就给整理方案"的后路：
    # 那会让模型拿本地文件冒充远端内容。
    "notion": "Notion 工作区工具：读取、汇总、整理页面与数据库内容时必须调用已挂载的 mcp__notion__* 工具。工具调用失败就如实说明失败原因，不得改用本地工作区文件，也不得凭空编造页面或条目。",
    "wecom": "企业微信协作助手：发送消息、查询会话与整理群协作内容时必须调用已挂载的 mcp__wecom__* 工具。工具调用失败就如实说明失败原因，不要编造消息记录或群成员。",
    "feishu": "飞书办公协作：读写文档、多维表格与会议信息时必须调用已挂载的 mcp__feishu__* 工具。工具调用失败就如实说明失败原因，不要编造文档内容或表格数据。",
    "image": "图像生成：根据文字描述构思高质量画面；给出清晰构图、风格与细节提示，必要时拆成可直接用于生图的提示词。",
}


def skill_notes(
    names: list[str],
    *,
    execute: bool | None = None,
    can_write: bool = True,
) -> str:
    """拼插件/能力说明。

    ``execute`` 有三种语义，别混：

    - ``True``：agent 通道 B，模型有 execute_skill_script / run_command，要求真正执行。
    - ``False``：plan 模式，只做计划参考，不得执行动作。
    - ``None``：ask 模式，用内置工具与已授权 MCP 直接完成，但不加载 SKILL.md、
      也不允许调用技能脚本（那些是「技能」，不是这里的「插件说明」）。

    ``can_write`` 决定要不要承诺「需要写文件时用对应写入工具」。没有写工具的用户
    收到这句话只会让模型反复尝试一个不存在的工具，所以按实际工具名单收窄措辞。
    """
    lines = [
        SKILL_INSTRUCTIONS[name]
        for name in names
        if name in SKILL_INSTRUCTIONS and is_enabled(f"plugin:{name}")
    ]
    if not lines:
        return ""
    if execute is True:
        head = "已启用插件/技能。按这些做法完成任务："
    elif execute is False:
        head = "这些插件/技能只作为计划参考，不要执行其中的动作："
    elif can_write:
        head = (
            "本次对话启用了这些能力，请用当前可用的内置工具与已授权 MCP 完成，"
            "不要声称自己无法处理文件；需要写文件时用对应写入工具："
        )
    else:
        head = (
            "本次对话启用了这些能力，请用当前可用的内置工具与已授权 MCP 完成。"
            "当前没有写文件或交付文件的工具，需要产出文件时先给出完整内容，"
            "并说明需要更高权限才能生成文件："
        )
    return head + "\n" + "\n".join(f"- {line}" for line in lines)
