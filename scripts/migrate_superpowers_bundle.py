"""把之前按单个技能登记的 superpowers 迁移成「一个技能包」。

读取 skills_data/records 下 source_label=superpowers 且还没有 bundle_id 的记录，
统一打上同一个 bundle_id，让前端显示成一张卡片。
"""

from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RECORDS = ROOT / "skills_data" / "records"


def main() -> None:
    if not RECORDS.is_dir():
        raise SystemExit(f"找不到记录目录：{RECORDS}")
    bundle_id = "bu_" + secrets.token_hex(6)
    changed = 0
    for path in sorted(RECORDS.glob("sk_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("source_label") != "superpowers":
            continue
        if payload.get("bundle_id"):
            continue
        payload["bundle_id"] = bundle_id
        payload["bundle_name"] = "Superpowers"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        changed += 1
    print(f"bundle_id={bundle_id} changed={changed}")


if __name__ == "__main__":
    main()
