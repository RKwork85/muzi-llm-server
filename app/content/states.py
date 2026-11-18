"""内容创作任务的状态机。

`TaskStatus` 与 `TaskStage` 是唯一事实来源；`app/models/constants.py` 据此生成
持久化校验用的字符串元组（与 `app.agents.models.TaskStatus` 的既有模式一致）。

状态机（阶段归属内联在 ``TRANSITIONS`` 里）：

    collect 阶段:  queued -> running -> draft          采集完成，可预览
                    draft   -> queued                  提交任务节点（切到 workflow 阶段）
    workflow 阶段: queued -> running -> succeeded/failed
                    failed  -> queued                  重试
                    succeeded -> publishing            进入发布
    publish 阶段:  publishing -> published / publish_failed
                    publish_failed -> publishing       发布重试
    任意排队中:    queued -> canceled                  用户取消（未进 GPU/LLM，零成本）

注意：``draft`` 只出现在 collect 阶段；``publishing`` / ``publish_failed`` 只出现在
publish 阶段；``queued`` / ``running`` 在 collect 与 workflow 两阶段含义不同，
由 ``stage`` 字段区分。
"""

from __future__ import annotations

from enum import StrEnum


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DRAFT = "draft"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    PUBLISH_FAILED = "publish_failed"
    CANCELED = "canceled"


class TaskStage(StrEnum):
    COLLECT = "collect"
    WORKFLOW = "workflow"
    PUBLISH = "publish"


# 终态：不会再发生任何流转。
TERMINAL_STATUSES = frozenset({TaskStatus.PUBLISHED, TaskStatus.CANCELED})

# (stage, status) -> 允许的下一状态集合。
TRANSITIONS: dict[tuple[TaskStage, TaskStatus], frozenset[TaskStatus]] = {
    (TaskStage.COLLECT, TaskStatus.QUEUED): frozenset(
        {TaskStatus.RUNNING, TaskStatus.CANCELED, TaskStatus.FAILED}
    ),
    (TaskStage.COLLECT, TaskStatus.RUNNING): frozenset(
        {TaskStatus.DRAFT, TaskStatus.FAILED, TaskStatus.CANCELED}
    ),
    (TaskStage.COLLECT, TaskStatus.DRAFT): frozenset(
        {TaskStatus.QUEUED, TaskStatus.FAILED, TaskStatus.CANCELED}
    ),
    (TaskStage.COLLECT, TaskStatus.FAILED): frozenset({TaskStatus.QUEUED}),
    (TaskStage.WORKFLOW, TaskStatus.QUEUED): frozenset(
        {TaskStatus.RUNNING, TaskStatus.CANCELED, TaskStatus.FAILED}
    ),
    (TaskStage.WORKFLOW, TaskStatus.RUNNING): frozenset(
        {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELED}
    ),
    (TaskStage.WORKFLOW, TaskStatus.FAILED): frozenset({TaskStatus.QUEUED}),
    (TaskStage.WORKFLOW, TaskStatus.SUCCEEDED): frozenset({TaskStatus.PUBLISHING}),
    (TaskStage.PUBLISH, TaskStatus.PUBLISHING): frozenset(
        {TaskStatus.PUBLISHED, TaskStatus.PUBLISH_FAILED}
    ),
    (TaskStage.PUBLISH, TaskStatus.PUBLISH_FAILED): frozenset({TaskStatus.PUBLISHING}),
}


def can_transition(
    stage: TaskStage | str, current: TaskStatus | str, target: TaskStatus | str
) -> bool:
    """判断当前 (stage, status) 是否允许流转到 target。"""
    try:
        stage = TaskStage(stage)
        current = TaskStatus(current)
        target = TaskStatus(target)
    except ValueError:
        return False
    return target in TRANSITIONS.get((stage, current), frozenset())


def is_terminal(status: TaskStatus | str) -> bool:
    try:
        return TaskStatus(status) in TERMINAL_STATUSES
    except ValueError:
        return False
