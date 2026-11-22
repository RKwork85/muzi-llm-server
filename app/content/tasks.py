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
from app.content.qs_client import qs_client_from_settings
from app.content.service import ContentService
from app.content.states import TaskStage, TaskStatus, is_terminal
from app.core.config import load_settings
from app.core.database.redis import init_redis
from app.core.database.session import get_session_factory, init_engine
from app.models.content import ContentTask

logger = logging.getLogger("muzi_llm")

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


def _looks_like_preview(value: Any) -> bool:
    """判断节点是否是 qs 的 preview_data（而非其中某条 note）。"""
    if not isinstance(value, dict):
        return False
    notes = value.get("notes")
    if not isinstance(notes, list) or not notes:
        return False
    # preview 的 notes 是笔记对象列表；note 自身不会有 notes 字段。
    return any(isinstance(n, dict) for n in notes[:3]) and any(
        key in value for key in ("batch", "author", "platform")
    )


def _extract_preview(result: dict[str, Any]) -> dict[str, Any] | None:
    """从 qs 采集返回里提取 preview_data。

    qs 的 ``POST /api/collect/run`` 返回形如
    ``{ platform, urls, results: [{ kind, result: { ..., preview } }] }``，
    preview 位于 ``results[].result.preview``（note_batch 分支）。

    这里用「广度优先 + 结构识别」而不是写死层级：上游只要把 preview 放在任意
    嵌套层（顶层 / results[].result / results[].preview …）都能取到，避免结构
    微调后又出现「采集成功但未返回 preview_data」的假失败。
    """
    queue: list[Any] = [result]
    seen: set[int] = set()
    while queue:
        node = queue.pop(0)
        if id(node) in seen:
            continue
        seen.add(id(node))
        if _looks_like_preview(node):
            return node
        if isinstance(node, dict):
            # preview 优先，其次是常见的包裹层；其余值一并入队兜底。
            for key in ("preview", "results", "result", "data"):
                if key in node:
                    queue.append(node[key])
            if not any(key in node for key in ("preview", "results", "result", "data")):
                queue.extend(node.values())
        elif isinstance(node, list):
            queue.extend(node)

    # 提取失败时记录返回结构骨架，便于定位根因（不打印媒体直链等大字段）。
    logger.warning("采集结果未提取到 preview_data，返回结构: %s", _shape_of(result))
    return None


def _shape_of(value: Any, depth: int = 0) -> Any:
    """把返回值压成「类型/键名」骨架，日志里一眼看出 preview 挂在哪一层。"""
    if depth >= 4:
        return "…"
    if isinstance(value, dict):
        return {k: _shape_of(v, depth + 1) for k, v in list(value.items())[:12]}
    if isinstance(value, list):
        head = _shape_of(value[0], depth + 1) if value else None
        return [f"list[{len(value)}]", head]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return type(value).__name__
    return type(value).__name__


async def run_collect(session: AsyncSession, task: ContentTask) -> None:
    """采集任务：queued → running → draft（直调 qs-datacapture 采集服务）。

    后端作为调度中心，把 user_id / task_id 透传给采集服务；采集服务内部完成
    Playwright 采集 + 媒体直传 muzi-oss，返回 preview_data（含 OSS 公网 url）。
    第一期仅支持小红书笔记直采，按 platform 过滤出 xhs 链接后走 xhs.crawl_batch。
    """
    service = ContentService()
    await service.transition(session, task, status=TaskStatus.RUNNING)

    client = qs_client_from_settings(load_settings())
    if client is None:
        raise RuntimeError("QS_DATACAPTURE_BASE_URL 未配置，无法执行采集")

    xhs_urls = [
        str(link.get("url") or "").strip()
        for link in (task.source_links or [])
        if str(link.get("platform") or "").lower() == "xhs"
        and str(link.get("url") or "").strip()
    ]
    if not xhs_urls:
        raise RuntimeError("暂无支持的采集链接（第一期仅支持小红书）")

    result = await client.collect(
        type_="xhs.crawl_batch",
        params={"urls": xhs_urls},
        user_id=str(task.user_id),
        task_id=str(task.id),
    )
    preview = _extract_preview(result)
    if not isinstance(preview, dict):
        raise RuntimeError("采集服务未返回 preview_data")

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
