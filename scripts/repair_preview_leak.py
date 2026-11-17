"""清理「工具参数预览」泄漏进回答正文的历史脏数据。

背景：早期 `_WritePreview` 把 `write_text_file` 的 `content` 参数当作普通
`delta.content` 推给前端，于是整段生成脚本被拼进助手消息正文并持久化。
真正的回答紧跟在脚本后面，且与服务端 `messages` 表里那一轮 assistant 回答完全一致。

本脚本以服务端回答为「真值」，把 `chat_history_messages.content_text` 从脚本中截断到
真实回答的起点，同时同步修复二次编码的 `content` 列。默认 dry-run，加 `--apply` 才写库，
并在写入前把原始值备份到 `scripts/backups/`。

用法：
    python scripts/repair_preview_leak.py          # 只报告
    python scripts/repair_preview_leak.py --apply  # 实际修复
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database.urls import build_async_postgres_url  # noqa: E402

# 生成脚本的强特征。命中任一即视为候选，再靠「服务端回答是否是后缀」确认，避免误伤
# 正文里正常提到 docx 的回答。
CODE_MARKERS = (
    'require("docx")',
    "require('docx')",
    "from docx import",
    "import docx",
    "Packer",
    "Document({",
    "python-docx",
    "docx.Document",
)

MIN_SUFFIX = 200  # 太短的「后缀匹配」不可信
BACKUP_DIR = Path(__file__).resolve().parent / "backups"


def dsn() -> str:
    return build_async_postgres_url(get_settings()).replace("postgresql+asyncpg://", "postgresql://")


def decode_content(raw: object) -> str | None:
    """`content` 列存的是 JSON 字符串标量（二次编码）。解不开就放弃同步该列。"""
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, str) else None
    return None


async def find_cut(text: str, answers: list[str]) -> int | None:
    """返回真实回答在 text 里的起点；无法可靠确定时返回 None。"""
    for answer in sorted(answers, key=len, reverse=True):
        if len(answer) < MIN_SUFFIX:
            continue
        index = text.find(answer)
        if index > 0 and text[index:] == answer:
            return index
    # 退一步：用回答末尾 200 字定位，但要求截断后只剩短前缀时仍带代码特征
    for answer in answers:
        if len(answer) < MIN_SUFFIX:
            continue
        tail = answer[-MIN_SUFFIX:]
        index = text.find(tail)
        if index > 0:
            return index - (len(answer) - len(tail))
    return None


async def run(apply: bool) -> int:
    conn = await asyncpg.connect(dsn())
    fixed = 0
    skipped = 0
    backup: list[dict[str, object]] = []
    try:
        rows = await conn.fetch(
            """
            select m.id, m.seq, m.content_text, m.content, m.session_id,
                   s.chat_id, s.server_conversation_id
              from chat_history_messages m
              join chat_history_sessions s on s.id = m.session_id
             where m.role = 'assistant'
               and coalesce(m.content_text, '') <> ''
             order by s.chat_id, m.seq
            """
        )
        print(f"扫描 assistant 消息 {len(rows)} 条")
        for row in rows:
            text = row["content_text"] or ""
            if not any(marker in text for marker in CODE_MARKERS):
                continue
            conv_id = row["server_conversation_id"]
            if conv_id is None:
                print(f"[skip] chat={row['chat_id']} seq={row['seq']} 无 server_conversation_id")
                skipped += 1
                continue
            answers = [
                item["content"]
                for item in await conn.fetch(
                    "select content from messages where session_id = $1 and role = 'assistant'",
                    conv_id,
                )
            ]
            cut = await find_cut(text, answers)
            if cut is None or cut <= 0:
                print(f"[skip] chat={row['chat_id']} seq={row['seq']} 找不到可信的截断点")
                skipped += 1
                continue
            cleaned = text[cut:]
            raw_content = row["content"]
            decoded = decode_content(raw_content)
            print(
                f"[{'fix' if apply else 'dry'}] chat={row['chat_id']} seq={row['seq']} "
                f"{len(text)} -> {len(cleaned)} 字符（去掉 {cut} 字预览）"
            )
            print(f"       泄漏片段开头: {json.dumps(text[:70], ensure_ascii=False)}")
            print(f"       保留回答开头: {json.dumps(cleaned[:70], ensure_ascii=False)}")
            backup.append(
                {
                    "id": str(row["id"]),
                    "chat_id": row["chat_id"],
                    "seq": row["seq"],
                    "content_text": text,
                    "content": raw_content if isinstance(raw_content, (str, dict, list)) else None,
                }
            )
            if apply:
                await conn.execute(
                    """
                    update chat_history_messages
                       set content_text = $2,
                           content = case when $3::boolean then to_jsonb($2::text) else content end
                     where id = $1
                    """,
                    row["id"],
                    cleaned,
                    decoded is not None,
                )
            fixed += 1

        if apply and backup:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            path = BACKUP_DIR / f"preview_leak_{stamp}.json"
            path.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"备份已写入 {path}")

        print(f"\n待修复 {fixed} 条，跳过 {skipped} 条{'（已写入）' if apply else '（dry-run，未写库）'}")
        return 0
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="清理预览泄漏进回答正文的历史消息")
    parser.add_argument("--apply", action="store_true", help="实际写库（默认只报告）")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.apply)))


if __name__ == "__main__":
    main()
