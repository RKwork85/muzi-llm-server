"""官方工作流案例的内置数据 seed。

官方工作流案例由后端 ``flow_official_workflows`` 表驱动：登录用户看到的列表 =
「内置案例 + 超管新增 / 导入的动态案例」。这里的 ``BUILTIN_OFFICIAL_WORKFLOWS``
是内置案例的初始数据（与前端 ``workspace.config.js`` 的 ``FLOW_WORKSPACES`` 里
的 workflows 保持一致；前端那份仍作为「游客 / 后端未加载」时的兜底展示）。

seed 只补缺失的 ``(workspace_key, workflow_key)``，绝不覆盖：
- 超管删掉的内置案例（软删）不会被恢复；
- 超管改过的名称 / 顺序不会被刷回。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.flow import FlowOfficialWorkflow

# 内置案例：workspace_key / workflow_key / name / icon / desc / tags / status / demo。
# demo 关联前端打包的画布 JSON；动态案例的 demo 为 'blank'（空白）或 'official'（导入）。
BUILTIN_OFFICIAL_WORKFLOWS: tuple[dict, ...] = (
    # ---- AI 媒体创作 ----
    {
        "workspace_key": "meidiacreat",
        "workflow_key": "text2image",
        "name": "文生图案例",
        "icon": "t2i",
        "desc": "一句提示词生成单张图片，直接复用现有画布与 ComfyUI / 方舟执行器。",
        "tags": ["文生图", "ComfyUI", "方舟"],
        "status": "ready",
        "demo": "text2image",
        "sort_order": 0,
    },
    {
        "workspace_key": "meidiacreat",
        "workflow_key": "multi-reference",
        "name": "多图融合案例",
        "icon": "image",
        "desc": "1~10 张参考图融合：图1 为编辑目标，其余提供人物 / 服装 / 场景素材，提示词用 <image1> 指代。",
        "tags": ["多图融合", "Qwen Image 2.1", "ComfyUI"],
        "status": "ready",
        "demo": "multi-reference",
        "sort_order": 1,
    },
    {
        "workspace_key": "meidiacreat",
        "workflow_key": "text2video",
        "name": "文生视频案例",
        "icon": "t2v",
        "desc": "一段文字生成短视频：文本节点写分镜与台词，连到文生视频节点后按所选模型出片。",
        "tags": ["文生视频", "MiniMax H3", "分镜"],
        "status": "ready",
        "demo": "text2video",
        "sort_order": 2,
    },
    {
        "workspace_key": "meidiacreat",
        "workflow_key": "image2video",
        "name": "图生视频案例",
        "icon": "i2v",
        "desc": "一张参考图作为首帧，配合提示词生成短视频。所有人打开看到的是同一份官方会话。",
        "tags": ["图生视频", "首帧", "MiniMax"],
        "status": "ready",
        "demo": "image2video",
        "sort_order": 3,
    },
    {
        "workspace_key": "meidiacreat",
        "workflow_key": "firstlast-frame",
        "name": "首尾帧生成视频案例",
        "icon": "flf",
        "desc": "首帧 + 尾帧 + 运镜提示词：单镜头连续过渡，生成首尾画面无缝衔接的短视频。",
        "tags": ["首尾帧", "MiniMax", "运镜"],
        "status": "ready",
        "demo": "firstlast-frame",
        "sort_order": 4,
    },
    # ---- 小红书 ----
    {
        "workspace_key": "xhs",
        "workflow_key": "note-generate",
        "name": "单篇笔记采集参考案例",
        "icon": "note",
        "desc": "粘贴笔记链接采集正文、作者与配图：图文与视频笔记各一条样例，资产管理器里可直接回看笔记模板页。",
        "tags": ["笔记采集", "小红书", "资产管理"],
        "status": "ready",
        "demo": "xhs-note-collect",
        "sort_order": 0,
    },
    {
        "workspace_key": "xhs",
        "workflow_key": "profile-design",
        "name": "个人主页设计参考案例",
        "icon": "profile",
        "desc": "头像、简介、封面与置顶笔记的主页视觉方案，当前为占位界面。",
        "tags": ["主页", "占位"],
        "status": "placeholder",
        "demo": "text2image",
        "sort_order": 1,
    },
    # ---- 抖音 ----
    {
        "workspace_key": "douyin",
        "workflow_key": "post-generate",
        "name": "单篇图文生成参考案例",
        "icon": "grid",
        "desc": "多图串文、封面标题与话题标签的组合生成，当前为占位界面。",
        "tags": ["图文", "占位"],
        "status": "placeholder",
        "demo": "text2image",
        "sort_order": 0,
    },
    {
        "workspace_key": "douyin",
        "workflow_key": "profile-design",
        "name": "个人主页设计参考案例",
        "icon": "profile",
        "desc": "主页头图、简介与作品封面的统一视觉方案，当前为占位界面。",
        "tags": ["主页", "占位"],
        "status": "placeholder",
        "demo": "text2image",
        "sort_order": 1,
    },
)


async def seed_official_workflows(session: AsyncSession) -> int:
    """补齐内置官方工作流案例，返回本次新增条数。幂等，不覆盖超管的改动。"""
    existing = set(
        await session.scalars(
            select(
                FlowOfficialWorkflow.workspace_key,
                FlowOfficialWorkflow.workflow_key,
            )
        )
    )
    created = 0
    for item in BUILTIN_OFFICIAL_WORKFLOWS:
        key = (item["workspace_key"], item["workflow_key"])
        if key in existing:
            continue
        session.add(
            FlowOfficialWorkflow(
                workspace_key=item["workspace_key"],
                workflow_key=item["workflow_key"],
                name=item["name"],
                icon=item["icon"],
                description=item["desc"],
                tags=item["tags"],
                status=item["status"],
                demo=item["demo"],
                sort_order=item["sort_order"],
                snapshot={},
            )
        )
        existing.add(key)
        created += 1
    await session.flush()
    return created
