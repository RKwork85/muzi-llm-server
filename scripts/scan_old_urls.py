"""扫描数据库里所有文本/JSON 列，找出仍指向旧对外域名的 URL。

用法：
    python scripts/scan_old_urls.py                     # 只统计
    python scripts/scan_old_urls.py --old https://muzihhh.com --new https://oss.muzihhh.com
                                                        # 统计 + 生成 SQL（不执行）

为什么不清洗所有列：agent 消息正文里可能引用 URL（markdown 图片），
而 generated_assets.url 是结构化字段。两者的更新策略不同，所以先看清再动手。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

TEXT_TYPES = ("text", "character varying", "jsonb", "json")


def _dsn() -> str:
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "muzi_llm")
    user = os.getenv("POSTGRES_USER", "muzi_admin")
    pwd = os.getenv("POSTGRES_PASSWORD", "")
    return f"postgresql+asyncpg://{user}:{pwd}@{host}:{port}/{db}"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old", default="https://muzihhh.com/files/")
    parser.add_argument("--new", default="https://oss.muzihhh.com/files/")
    args = parser.parse_args()

    eng = create_async_engine(_dsn())
    async with eng.connect() as conn:
        cols = (
            await conn.execute(
                text(
                    """
                    SELECT table_name, column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND data_type = ANY(:types)
                    ORDER BY table_name, column_name
                    """
                ),
                {"types": list(TEXT_TYPES)},
            )
        ).all()

        hits: list[tuple[str, str, str, int]] = []
        for table, column, dtype in cols:
            try:
                sql = text(
                    f'SELECT count(*) FROM "{table}" WHERE "{column}"::text LIKE :pat'
                )
                n = (await conn.execute(sql, {"pat": f"%{args.old}%"})).scalar_one()
                if n:
                    hits.append((table, column, dtype, n))
            except Exception:  # noqa: BLE001 —— 视图/权限问题跳过即可
                continue

        print(f"扫描到 {len(cols)} 个文本列，命中 {len(hits)} 个：")
        total = 0
        for table, column, dtype, n in hits:
            print(f"  {table}.{column} ({dtype}) -> {n} 行")
            total += n
        if not hits:
            print("  （没有引用旧域名的数据）")

        if hits and args.new:
            print("\n--- 参考 SQL（未执行，请逐条确认后手动跑）---")
            for table, column, dtype, _ in hits:
                print(
                    f'UPDATE "{table}" SET "{column}" = '
                    f"replace(\"{column}\"::text, '{args.old}', '{args.new}')\n"
                    f"  WHERE \"{column}\"::text LIKE '%{args.old}%';"
                )
            print(f"\n合计受影响行（含重叠，仅作量级参考）：{total}")

    await eng.dispose()


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(main())
