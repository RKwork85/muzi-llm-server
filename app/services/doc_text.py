"""文档正文的纯文本处理。

摘要要同时被「我的文档」和「公开文档」两处列表使用，放在这里而不是某个
endpoint 里，避免两份正则各自漂移、同一条数据在两处显示不同的摘要。
"""

from __future__ import annotations

import re


def excerpt(content: str, limit: int = 120) -> str:
    """从 markdown 正文提取无格式摘要，用于列表展示。"""
    text = content or ""
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[#>*_`~\-|]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def author_name(display_name: str | None, username: str | None) -> str:
    """公开页展示的作者名。两者都缺失时给出中性占位，不暴露邮箱等敏感标识。"""
    return (display_name or "").strip() or (username or "").strip() or "匿名作者"
