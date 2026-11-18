"""内容创作任务执行（Taskiq 队列封装 + 业务逻辑）。

第二阶段已接入 Taskiq：``run_collect`` / ``run_workflow`` / ``run_publish`` 保持
「纯业务函数」（接收 session + task，走状态机 + 发进度事件 + 产出 mock 结果），
外层 ``@broker.task`` 薄封装承接队列投递——开短事务、取任务、幂等检查、执行、提交。

任务按 ``stage`` 路由（``dispatch``）：

    collect   -> collect_task   : queued -> running -> draft（产出 preview_data）
    workflow  -> workflow_task  : queued -> running -> succeeded（链式子步骤 + 进度）
    publish   -> publish_task   : publishing -> published / publish_failed

真实逻辑（爬取 / LLM 改写 / comfy 配图 / 平台发布）在后续迭代替换
``TODO(第二阶段)`` 标记的占位段。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.broker import broker
from app.content.service import ContentService
from app.content.states import TaskStage, TaskStatus, is_terminal
from app.core.config import load_settings
from app.core.database.redis import init_redis
from app.core.database.session import get_session_factory, init_engine
from app.models.content import ContentTask

logger = logging.getLogger(__name__)

# 与前端 creation.config.js 的 WORKFLOW_NODES 对齐（占位，仅用于骨架演示）。
_WORKFLOW_STEPS: list[tuple[str, str]] = [
    ("collect", "数据采集"),
    ("clean", "内容清洗"),
    ("rewrite", "智能改写"),
    ("generate", "素材生成"),
    ("compose", "排版组装"),
    ("review", "质检风控"),
    ("publish", "交付发布"),
]


async def run_collect(session: AsyncSession, task: ContentTask) -> None:
    """采集任务：queued → running → draft。"""
    service = ContentService()
    await service.transition(session, task, status=TaskStatus.RUNNING)
    # TODO(第二阶段): 按 task.source_links 逐平台抓取 + 解析正文，产出 preview_data。
    await asyncio.sleep(0)  # 占位：真实实现为网络抓取
    preview = {
        "title": "（占位）采集到的原文标题",
        "summary": "（占位）采集到的正文摘要",
        "platforms": [link.get("platform") for link in (task.source_links or [])],
    }
    await service.transition(
        session, task, status=TaskStatus.DRAFT, preview_data=preview
    )


async def run_workflow(session: AsyncSession, task: ContentTask) -> None:
    """工作流任务：queued → running → succeeded（链式子步骤 + 进度事件）。"""
    service = ContentService()
    await service.transition(session, task, status=TaskStatus.RUNNING)
    total = len(_WORKFLOW_STEPS)
    for i, (key, name) in enumerate(_WORKFLOW_STEPS, start=1):
        progress = {
            "percent": round(i / total * 100),
            "current_step": i,
            "total_steps": total,
            "step_key": key,
            "step_name": name,
        }
        await service.report_progress(session, task, progress)
        # TODO(第二阶段): 真正的洗稿 → 改写 → 配图(comfy) → 排版逻辑。
        await asyncio.sleep(0)  # 占位：真实实现为 LLM/comfy 调用
    result = {
        "title": "（占位）AI 时代，内容创作者如何靠「搬运」也能做出爆款",
        "deliver_type": task.deliver_type,
        "ref": None,  # TODO: 交付物上传 OSS 后回填 result_ref
    }
    await service.transition(session, task, status=TaskStatus.SUCCEEDED, result=result)


async def run_publish(session: AsyncSession, task: ContentTask) -> None:
    """发布任务：publishing → published / publish_failed。"""
    service = ContentService()
    # TODO(第二阶段): 读取平台凭证(密文) + 调平台 API 发布，成功后回填 asset_url。
    await asyncio.sleep(0)  # 占位：真实实现为平台发布调用
    await service.transition(
        session,
        task,
        status=TaskStatus.PUBLISHED,
        stage=TaskStage.PUBLISH,
        result={"asset_url": None},
    )


async def dispatch(session: AsyncSession, task: ContentTask) -> None:
    """按 stage 路由到对应业务函数。Taskiq 任务与（旧）轮询 worker 共用此入口。"""
    if task.stage == TaskStage.COLLECT.value:
        await run_collect(session, task)
    elif task.stage == TaskStage.WORKFLOW.value:
        await run_workflow(session, task)
    elif task.stage == TaskStage.PUBLISH.value:
        await run_publish(session, task)
    else:
        raise RuntimeError(f"未知任务阶段: {task.stage}")


# ---------------------------------------------------------------------------
# Taskiq 队列封装
# ---------------------------------------------------------------------------

def _ensure_backends() -> None:
    """worker 进程惰性初始化 DB 引擎与 Redis（幂等，多进程各自持有一份）。"""
    settings = load_settings()
    init_engine(settings)
    init_redis(settings)


async def _execute(task_id: str, stage: TaskStage, expected: TaskStatus) -> None:
    """短事务执行单个任务：取任务 → 幂等检查 → dispatch → 提交。失败落 failed 态。"""
    _ensure_backends()
    factory = get_session_factory()
    async with factory() as session:
        task = await session.get(ContentTask, uuid.UUID(task_id))
        if task is None:
            logger.warning("content task not found id=%s (可能已回滚/删除)", task_id)
            return
        if task.stage != stage.value or task.status != expected.value:
            # 幂等：任务已被取消/重复投递/状态已推进，直接跳过。
            logger.info(
                "content task id=%s skip: expect=%s:%s actual=%s:%s",
                task_id, stage.value, expected.value, task.stage, task.status,
            )
            return
        try:
            await dispatch(session, task)
            await session.commit()
        except Exception as exc:  # noqa: BLE001 —— 任何失败都落 failed 态，不卡在队列。
            await session.rollback()
            await _mark_failed(factory, task_id, stage, exc)
            raise


async def _mark_failed(
    factory, task_id: str, stage: TaskStage, exc: Exception
) -> None:
    """把失败任务落到终态（failed / publish_failed），避免任务永远卡在 queued。"""
    failed_status = (
        TaskStatus.PUBLISH_FAILED if stage == TaskStage.PUBLISH else TaskStatus.FAILED
    )
    async with factory() as session:
        task = await session.get(ContentTask, uuid.UUID(task_id))
        if task is None or is_terminal(task.status):
            return
        service = ContentService()
        try:
            await service.transition(session, task, status=failed_status)
            task.error = (str(exc) or exc.__class__.__name__)[:2000]
            await session.commit()
        except Exception:  # noqa: BLE001 —— 失败标记失败只记日志，不掩盖原异常。
            await session.rollback()
            logger.exception("content task id=%s 标记失败态时出错", task_id)


@broker.task(task_name="content.collect")
async def collect_task(task_id: str) -> None:
    """采集收单入队任务。"""
    await _execute(task_id, TaskStage.COLLECT, TaskStatus.QUEUED)


@broker.task(task_name="content.workflow")
async def workflow_task(task_id: str) -> None:
    """任务节点提交后的工作流执行任务。"""
    await _execute(task_id, TaskStage.WORKFLOW, TaskStatus.QUEUED)


@broker.task(task_name="content.publish")
async def publish_task(task_id: str) -> None:
    """发布执行任务。"""
    await _execute(task_id, TaskStage.PUBLISH, TaskStatus.PUBLISHING)
