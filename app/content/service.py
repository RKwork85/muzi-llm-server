"""内容创作任务服务：状态机 + 生命周期 + SSE 事件发布。

所有写操作都走 ``transition`` 校验状态机，杜绝绕过。任务真相落 PostgreSQL，
进度与 SSE 广播走 Redis（跨进程）。

``session`` 由调用方注入：API 层用请求级 ``DbSession``（handler 返回后 commit），
worker 进程用自己的短事务（dispatch 后 commit）。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content import events
from app.content.states import TaskStage, TaskStatus, can_transition, is_terminal
from app.core.exceptions import ContentTaskNotFoundError, ContentTaskStateError
from app.models.content import ContentTask

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ContentService:
    """内容创作任务生命周期。"""

    # ---------- 收单 / 提交 / 查询 / 取消 ----------

    async def create_collect_task(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        links: list[Any],
    ) -> ContentTask:
        """采集收单：新建任务（queued / collect），入队等待采集 worker。"""
        task = ContentTask(
            user_id=user_id,
            status=TaskStatus.QUEUED.value,
            stage=TaskStage.COLLECT.value,
            source_links=[link.model_dump() for link in links],
            queued_at=_utcnow(),
        )
        session.add(task)
        await session.flush()  # 拿到 id 再发事件
        await events.publish(
            user_id,
            {
                "type": "content.task.state",
                "task_id": str(task.id),
                "status": task.status,
                "stage": task.stage,
            },
        )
        return task

    async def submit(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        task_id: uuid.UUID,
        deliver_type: str,
        config: dict[str, Any],
    ) -> ContentTask:
        """提交任务节点：draft/collect → queued/workflow，写入配置与交付链路。"""
        task = await self._get_owned(session, user_id, task_id)
        task.deliver_type = deliver_type
        task.config = config
        await self.transition(
            session, task, status=TaskStatus.QUEUED, stage=TaskStage.WORKFLOW
        )
        return task

    async def get_task(
        self, session: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID
    ) -> ContentTask:
        return await self._get_owned(session, user_id, task_id)

    async def list_tasks(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> tuple[list[ContentTask], int]:
        filters = [ContentTask.user_id == user_id]
        if status:
            filters.append(ContentTask.status == status)
        total = await session.scalar(
            select(func.count()).select_from(ContentTask).where(*filters)
        )
        rows = list(
            await session.scalars(
                select(ContentTask)
                .where(*filters)
                .order_by(ContentTask.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        return rows, total or 0

    async def cancel(
        self, session: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID
    ) -> ContentTask:
        """取消排队/运行中的任务。已产出结果的终态不可取消。"""
        task = await self._get_owned(session, user_id, task_id)
        if task.status not in (TaskStatus.QUEUED.value, TaskStatus.RUNNING.value):
            raise ContentTaskStateError("任务当前状态无法取消")
        await self.transition(session, task, status=TaskStatus.CANCELED)
        return task

    async def start_publish(
        self, session: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID
    ) -> ContentTask:
        """发起发布：succeeded/workflow → publishing/publish，入队发布 worker。"""
        task = await self._get_owned(session, user_id, task_id)
        await self.transition(
            session, task, status=TaskStatus.PUBLISHING, stage=TaskStage.PUBLISH
        )
        return task

    # ---------- 状态机 ----------

    async def transition(
        self,
        session: AsyncSession,
        task: ContentTask,
        *,
        status: TaskStatus,
        stage: TaskStage | None = None,
        progress: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        preview_data: dict[str, Any] | None = None,
    ) -> ContentTask:
        """按状态机流转。``stage`` 可与状态一并变化（如 submit 时 collect→workflow）。"""
        new_status = TaskStatus(status)
        new_stage = TaskStage(stage) if stage is not None else TaskStage(task.stage)

        if not can_transition(task.stage, task.status, new_status):
            raise ContentTaskStateError(
                f"不允许从 {task.stage}:{task.status} 流转到 {new_stage.value}:{new_status.value}"
            )

        task.status = new_status.value
        task.stage = new_stage.value

        if progress is not None:
            task.progress = progress
        if preview_data is not None:
            task.preview_data = preview_data
        if result is not None:
            task.result = result
        if error is not None:
            task.error = error

        now = _utcnow()
        if new_status == TaskStatus.QUEUED:
            task.queued_at = now
        if new_status == TaskStatus.RUNNING and task.started_at is None:
            task.started_at = now
        if is_terminal(new_status) or new_status in (
            TaskStatus.DRAFT,
            TaskStatus.SUCCEEDED,
        ):
            task.finished_at = now

        await session.flush()
        await self._emit_state(task)
        return task

    async def report_progress(
        self, session: AsyncSession, task: ContentTask, progress: dict[str, Any]
    ) -> None:
        """worker 写进度 + 广播进度事件（不改变状态）。"""
        task.progress = progress
        await session.flush()
        await events.publish(
            task.user_id,
            {
                "type": "content.task.progress",
                "task_id": str(task.id),
                "status": task.status,
                "stage": task.stage,
                "progress": progress,
            },
        )

    # ---------- 内部 ----------

    async def _get_owned(
        self, session: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID
    ) -> ContentTask:
        task = await session.get(ContentTask, task_id)
        if task is None or task.user_id != user_id:
            raise ContentTaskNotFoundError()
        return task

    async def _emit_state(self, task: ContentTask) -> None:
        await events.publish(
            task.user_id,
            {
                "type": "content.task.state",
                "task_id": str(task.id),
                "status": task.status,
                "stage": task.stage,
                "error": task.error,
                "result": task.result,
            },
        )
