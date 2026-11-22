"""内容创作任务 API：收单（collect）/ 提交（submit）/ 查询 / 取消 / 发布 / SSE 进度。

登录态鉴权（``get_authenticated_user``）；SSE 用 ``get_stream_user_id``（短事务）
避免长连接占住数据库连接池。user_id 一律从登录态注入，杜绝伪造身份。

任务真相在 PostgreSQL，本接口只做收单/查询/取消与状态机推进；真正的执行由
独立 worker 进程（``python -m app.content.worker``，Taskiq 消费）完成。

投递在 handler 内「显式 ``commit`` 后同步 ``.kiq()``」：FastAPI 的 ``BackgroundTasks``
实际在依赖 teardown（``get_db_session`` 的 commit）之前执行，若用它投递，worker
会先于 commit 读到旧状态而跳过。显式提交后再投递，保证任务行落库与入队严格有序。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.api.deps import get_authenticated_user, get_stream_user_id
from app.authorization.dependencies import require_permission
from app.content import events
from app.content.schemas import (
    CollectRequest,
    CollectResponse,
    SubmitRequest,
    TaskListOut,
    TaskOut,
)
from app.content.service import ContentService
from app.content.tasks import (
    collect_task as enqueue_collect,
    publish_task as enqueue_publish,
    workflow_task as enqueue_workflow,
)
from app.core.database.deps import DbSession
from app.models.tables import User

router = APIRouter(prefix="/content", tags=["content"])

# 创作是写操作：进得了创作页不等于能发起任务。查询/SSE 不加校验（高频轮询）。
ContentWriter = Annotated[User, Depends(require_permission("content.write"))]


@router.post("/collect", response_model=CollectResponse, summary="采集收单（登录态）")
async def collect(
    body: CollectRequest,
    user: ContentWriter,
    session: DbSession,
) -> CollectResponse:
    """粘贴链接 → 新建采集任务（queued/collect），返回 task_id。"""
    task = await ContentService().create_collect_task(session, user.id, body.links)
    # 先落库再入队：显式 commit 后再 kiq，确保 worker 读到的必是已提交的 queued 态。
    await session.commit()
    await enqueue_collect.kiq(str(task.id))
    return CollectResponse(task_id=task.id, status=task.status, stage=task.stage)


@router.post("/submit", response_model=TaskOut, summary="提交任务节点（登录态）")
async def submit(
    body: SubmitRequest,
    user: ContentWriter,
    session: DbSession,
) -> TaskOut:
    """采集预览确认后：draft → queued/workflow，写入交付链路与配置。"""
    task = await ContentService().submit(
        session, user.id, body.task_id, body.deliver_type, body.config
    )
    await session.commit()
    await enqueue_workflow.kiq(str(task.id))
    return TaskOut.model_validate(task)


@router.get("/tasks", response_model=TaskListOut, summary="我的创作任务列表")
async def list_tasks(
    user: Annotated[User, Depends(get_authenticated_user)],
    session: DbSession,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> TaskListOut:
    rows, total = await ContentService().list_tasks(
        session, user.id, limit=limit, offset=offset, status=status
    )
    return TaskListOut(
        items=[TaskOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/tasks/{task_id}", response_model=TaskOut, summary="查询任务状态")
async def get_task(
    task_id: uuid.UUID,
    user: Annotated[User, Depends(get_authenticated_user)],
    session: DbSession,
) -> TaskOut:
    task = await ContentService().get_task(session, user.id, task_id)
    return TaskOut.model_validate(task)


@router.post("/tasks/{task_id}/cancel", response_model=TaskOut, summary="取消排队/运行中的任务")
async def cancel_task(
    task_id: uuid.UUID,
    user: ContentWriter,
    session: DbSession,
) -> TaskOut:
    task = await ContentService().cancel(session, user.id, task_id)
    return TaskOut.model_validate(task)


@router.post("/tasks/{task_id}/publish", response_model=TaskOut, summary="发起发布（登录态）")
async def publish_task(
    task_id: uuid.UUID,
    user: ContentWriter,
    session: DbSession,
) -> TaskOut:
    """succeeded → publishing，交由发布 worker 完成（骨架阶段为占位）。"""
    task = await ContentService().start_publish(session, user.id, task_id)
    await session.commit()
    await enqueue_publish.kiq(str(task.id))
    return TaskOut.model_validate(task)


@router.get("/tasks/{task_id}/events", summary="SSE：订阅单任务进度")
async def stream_task_events(
    task_id: uuid.UUID,
    request: Request,
    user_id: Annotated[str, Depends(get_stream_user_id)],
) -> StreamingResponse:
    """登录态 SSE 长连接，订阅指定任务的进度与状态流转。

    事件名即 ``type``（如 ``content.task.progress`` / ``content.task.state``），
    ``data`` 为 JSON。空闲每 15 秒发注释帧 ``: ping`` 保活。
    """

    async def body() -> AsyncIterator[bytes]:
        yield b": connected\n\n"
        async for event in events.subscribe(user_id):
            if await request.is_disconnected():
                break
            if event is None:
                yield b": ping\n\n"
                continue
            # 同一用户可能有多个任务，按 task_id 过滤出本任务的进度。
            if event.get("task_id") != str(task_id):
                continue
            frame = (
                f"event: {event.get('type', 'message')}\n"
                f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            )
            yield frame.encode("utf-8")

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(body(), media_type="text/event-stream", headers=headers)
