"""workflow_id → 任务类型映射。

「任务类型」是网关「全局消息队列排队管控」的配置维度：走 comfy 网关路线的不同
workflow 归入同一任务类型后，共享一份「全局排队上限」（网关 ``comfy_task_queue_max``，
默认 50，按任务类型可覆盖）。网关据此在 Redis 里按 task_type 全局计数（不分用户），
主服务据此把 workflow_id 归到任务类型用于额度展示。两边映射必须一致。

ComfyUI 单实例串行执行、不存在真正并发，因此原先的「每用户角色并发额度」已废弃：
控制权收回为「任务类型全局排队上限」，由网关 config 维护，主服务不再解析角色额度。
"""

from __future__ import annotations

# workflow_id → 任务类型。与网关 muzi-comfy-service/app/workflow_task_type.py 保持同步：
# 网关据此在 Redis 里按 task_type 全局计数，主服务据此把 workflow_id 归到任务类型，
# 两边映射必须一致，否则会把 A 类型的上限值套到 B 类型的计数上。
WORKFLOW_TASK_TYPES: dict[str, str] = {
    "muzi_moss_文生图": "create_media",
    "muzi-minimaxH3-文生视频": "create_media",
    "muzi_minimaxH3图生视频": "create_media",
    "muzi_minimaxH3首尾帧生成视频": "create_media",
    "muzi-minimaxH3四视图生成": "create_media",
    "muzi_image_qwen_image_2_1_turbo_多图融合": "create_media",
    "muzi_image_qwen_image_2_1_turbo_t2i": "create_media",
}

DEFAULT_TASK_TYPE = "create_media"


def task_type_of(workflow_id: str) -> str:
    """workflow_id → 任务类型；未登记的归默认任务类型。"""
    return WORKFLOW_TASK_TYPES.get(workflow_id, DEFAULT_TASK_TYPE)
