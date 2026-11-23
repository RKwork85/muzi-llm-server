"""应用启动和关闭时的数据库连接。失败只记录异常类型，不记录连接串。"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text

from app.core.config import ROOT, Settings
from app.core.database.redis import close_redis, get_redis, init_redis
from app.core.database.session import dispose_engine, get_session_factory, init_engine

logger = logging.getLogger("muzi_llm")


async def startup_data_backends(settings: Settings) -> bool:
    if not settings.database_startup_required:
        logger.info("数据库启动检查已关闭")
        return False
    try:
        init_engine(settings)
        init_redis(settings)
        await _wait_postgres(settings)
        await _warn_pending_migrations(settings)
        await _wait_redis(settings)
    except Exception:
        logger.error("数据后端启动失败")
        await close_redis()
        await dispose_engine()
        raise
    logger.info(
        "数据后端已就绪 postgres_host=%s postgres_db=%s redis_host=%s redis_db=%s",
        settings.postgres_host,
        settings.postgres_db,
        settings.redis_host,
        settings.redis_db,
    )
    return True


async def shutdown_data_backends(*, enabled: bool) -> None:
    if not enabled:
        return
    await close_redis()
    await dispose_engine()


async def _warn_pending_migrations(settings: Settings) -> None:
    """启动时检测「代码有迁移、数据库还没跟上」。

    ``AUTO_MIGRATE=true``（默认）时落后会自动执行 ``alembic upgrade head``，
    避免「忘跑迁移 → 请求 500」这类坑（如社区插件安装、官方工作流案例注册表）。
    生产可设 ``AUTO_MIGRATE=false`` 改走独立迁移流水线，此时只打印醒目警告，
    不阻塞、不自动迁移。
    """
    try:
        from alembic.script import ScriptDirectory

        script_dir = ScriptDirectory(str(ROOT / "alembic"))
        head = script_dir.get_current_head()
    except Exception as exc:
        logger.warning("迁移检测跳过 reason=script_dir_error type=%s", type(exc).__name__)
        return

    current: str | None = None
    try:
        factory = get_session_factory()
        async with factory() as session:
            row = await session.scalar(text("SELECT version_num FROM alembic_version"))
        if isinstance(row, str):
            current = row
    except Exception as exc:
        # 全新库、从未跑过迁移时 alembic_version 表不存在，同样要提醒。
        logger.warning("迁移检测读 alembic_version 失败 type=%s", type(exc).__name__)

    if current == head:
        return

    label = "从未初始化（无 alembic_version 表）" if current is None else current
    if settings.auto_migrate:
        logger.info("检测到数据库 schema 落后，开始自动迁移：%s -> %s", label, head)
        try:
            # env.py 在线迁移内部用 asyncio.run，不能在正在运行的事件循环里直接调，
            # 丢到新线程里执行，完成后回到主循环继续 seed。
            await asyncio.to_thread(_run_alembic_upgrade)
        except Exception as exc:
            logger.error(
                "自动迁移失败 type=%s error=%s，请手动执行 alembic upgrade head",
                type(exc).__name__,
                exc,
            )
            _log_migration_warning(label, head)
            return
        logger.info("自动迁移完成，schema 已更新到 %s", head)
        return

    _log_migration_warning(label, head)


def _log_migration_warning(label: str, head: str) -> None:
    logger.warning(
        "\n"
        "======================================================================\n"
        "  数据库 schema 落后于代码迁移，请执行：alembic upgrade head\n"
        f"  当前版本: {label}\n"
        f"  目标版本: {head}\n"
        "  否则涉及新列/新表的写入（如社区插件安装）会报 500 内部错误。\n"
        "======================================================================"
    )


def _run_alembic_upgrade() -> None:
    """在新线程里执行 alembic 迁移到 head。

    不传 config 文件路径：``Config()`` 会生成内存配置，``config_file_name`` 为 None，
    从而跳过 env.py 里的 ``fileConfig``，避免覆盖主进程的日志配置；连接串由 env.py
    自行按 Settings 构建，与命令行 ``python -m alembic upgrade head`` 等价。
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("prepend_sys_path", str(ROOT))
    command.upgrade(cfg, "head")


async def _wait_postgres(settings: Settings) -> None:
    factory = get_session_factory()
    delay = settings.db_startup_retry_seconds
    last_type = "Exception"
    for attempt in range(1, settings.db_startup_attempts + 1):
        try:
            async with factory() as session:
                await session.execute(text("SELECT 1"))
            return
        except Exception as exc:
            last_type = type(exc).__name__
            logger.warning("PostgreSQL 尚未就绪 attempt=%s error_type=%s", attempt, last_type)
            if attempt == settings.db_startup_attempts:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, 8)
    raise RuntimeError(f"PostgreSQL 不可用: {last_type}") from None


async def _wait_redis(settings: Settings) -> None:
    client = get_redis()
    delay = settings.db_startup_retry_seconds
    last_type = "Exception"
    for attempt in range(1, settings.db_startup_attempts + 1):
        try:
            pong = await client.ping()
            if pong is not True:
                raise RuntimeError("Redis ping 未返回 PONG")
            return
        except Exception as exc:
            last_type = type(exc).__name__
            logger.warning("Redis 尚未就绪 attempt=%s error_type=%s", attempt, last_type)
            if attempt == settings.db_startup_attempts:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, 8)
    raise RuntimeError(f"Redis 不可用: {last_type}") from None
