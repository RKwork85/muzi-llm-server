"""AI 画布上传图的孤儿回收。

分两步，互相独立：
1. 对账：把 ``flow_images.ref_count`` 拉回真实引用行数，修掉级联删除 / 手工清理
   造成的漂移；
2. 回收：引用归零且超过宽限期的图，先删数据库行（原子抢占，避免误删刚被引用的图），
   再删 OSS 对象。

宽限期默认 24 小时：刚上传、还没保存画布的图不会被当成孤儿。
"""

from __future__ import annotations

import asyncio
import logging

from app.core.config import Settings
from app.core.database.session import get_session_factory
from app.data.oss_client import OssUploadClient
from app.services.flow_store_service import FlowStoreService

logger = logging.getLogger("muzi_llm.flow")

# 启动后先等一会再跑第一轮，避免和进程启动时的其它初始化抢数据库连接。
_INITIAL_DELAY_SECONDS = 60


async def purge_once(
    settings: Settings,
    oss: OssUploadClient | None,
    *,
    user_id: object | None = None,
) -> tuple[int, int]:
    """跑一轮对账 + 回收，返回 (修正的计数行数, 删除的图片行数)。"""
    factory = get_session_factory()
    async with factory() as session:
        store = FlowStoreService(session)
        fixed = await store.recount_image_refs()
        removed = await store.purge_orphan_images(
            older_than_seconds=settings.flow_image_gc_grace_seconds,
            oss=oss,
            user_id=user_id,
        )
        await session.commit()
    return fixed, removed


async def run_flow_image_gc_loop(
    settings: Settings,
    oss: OssUploadClient | None,
    stop: asyncio.Event,
) -> None:
    """按 ``flow_image_gc_interval_seconds`` 周期回收；异常只记录，不退出循环。"""
    if not settings.flow_image_gc_enabled:
        return
    try:
        await asyncio.wait_for(stop.wait(), timeout=_INITIAL_DELAY_SECONDS)
        return
    except asyncio.TimeoutError:
        pass
    while not stop.is_set():
        try:
            fixed, removed = await purge_once(settings, oss)
            if fixed or removed:
                logger.info("flow image gc fixed=%s removed=%s", fixed, removed)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("flow image gc failed", exc_info=True)
        try:
            await asyncio.wait_for(
                stop.wait(), timeout=settings.flow_image_gc_interval_seconds
            )
        except asyncio.TimeoutError:
            continue
