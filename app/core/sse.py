"""SSE 帧编码。data 始终是一行 JSON。"""

from __future__ import annotations

import json
from typing import Any


def format_sse(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"
