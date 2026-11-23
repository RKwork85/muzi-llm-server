"""workflow_id → 任务类型映射测试。

额度管控已改为「任务类型全局排队上限」（网关 config 维护），主服务只负责
workflow_id → 任务类型映射，供额度展示使用。
"""

from __future__ import annotations

from app.authorization.application.task_quota_service import (
    DEFAULT_TASK_TYPE,
    task_type_of,
)


def test_task_type_of_maps_known_workflows() -> None:
    assert task_type_of("muzi_moss_文生图") == "create_media"
    assert task_type_of("muzi_image_qwen_image_2_1_turbo_多图融合") == "create_media"
    assert task_type_of("muzi-minimaxH3-文生视频") == "create_media"
    assert task_type_of("muzi_minimaxH3首尾帧生成视频") == "create_media"


def test_task_type_of_unknown_workflow_falls_back_to_default() -> None:
    assert task_type_of("some_future_workflow") == DEFAULT_TASK_TYPE == "create_media"
