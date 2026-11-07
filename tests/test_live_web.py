"""真实 Search / Fetch。默认跳过。WEB_LIVE=1 时各请求一次。"""

from __future__ import annotations

import os

import pytest

from app.tools.web_client import CURRENT_TOOL_BUDGET, DeepSeekWebClient
from app.core.config import Settings

pytestmark = pytest.mark.skipif(
    os.environ.get("WEB_LIVE") != "1",
    reason="设置 WEB_LIVE=1 才会请求真实 DeepSeek Search/Fetch",
)


def test_live_search_and_fetch_once() -> None:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        pytest.fail("真实联调未完成：环境变量 DEEPSEEK_API_KEY 为空")
    settings = Settings(
        _env_file=None,
        deepseek_api_key=key,
        deepseek_base_url="https://api.deepseek.com",
        deepseek_model="deepseek-flash",
        deepseek_model_allowlist="deepseek-flash,deepseek-v4-pro",
        web_tools_max_retries=0,
    )

    async def _run() -> None:
        client = DeepSeekWebClient(settings)
        await client.startup()
        token = CURRENT_TOOL_BUDGET.set(20)
        try:
            hits = await client.search("DeepSeek API 文档", 1)
            assert hits
            assert hits[0]["url"].startswith("https://")
            title, content = await client.fetch(hits[0]["url"])
            assert title or content
            assert key not in title
            assert key not in content
        finally:
            CURRENT_TOOL_BUDGET.reset(token)
            await client.aclose()

    import asyncio

    asyncio.run(_run())
