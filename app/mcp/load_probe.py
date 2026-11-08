"""可重复的 MCP 调用探针。短探针不能代替真实压测档位。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from app.mcp.deploy_check import LOAD_TIERS
from app.mcp.execution import MCPExecutionContext, run_mcp


async def run_probe(
    *,
    concurrency: int,
    seconds: float,
    call: Callable[[], Awaitable[str]] | None = None,
) -> dict[str, object]:
    started = time.perf_counter()
    deadline = started + seconds
    results: list[float] = []
    failures = 0

    async def one(index: int) -> None:
        nonlocal failures
        while time.perf_counter() < deadline:
            begin = time.perf_counter()
            try:
                ctx = MCPExecutionContext(
                    user_id=f"user-{index % max(concurrency, 1)}",
                    connector_id=f"probe-{index % 3}",
                    tool_name="echo",
                    local_name=f"mcp__probe-{index % 3}__echo",
                    request_id="probe",
                    timeout=1,
                    idempotent=True,
                )

                async def allow(item: MCPExecutionContext) -> bool:
                    del item
                    return True

                async def default_call() -> str:
                    await asyncio.sleep(0)
                    return "ok"

                await run_mcp(ctx, allow, call or default_call)
                results.append(time.perf_counter() - begin)
            except Exception:
                failures += 1

    await asyncio.gather(*(one(index) for index in range(max(concurrency, 1))))
    elapsed = time.perf_counter() - started
    ordered = sorted(results)
    total = len(results) + failures
    return {
        "kind": "local_probe",
        "production_load": False,
        "elapsed_seconds": round(elapsed, 3),
        "concurrency": concurrency,
        "requests": total,
        "success": len(results),
        "failures": failures,
        "success_rate": 0 if total == 0 else round(len(results) / total, 4),
        "p50_ms": _percentile(ordered, 0.50),
        "p95_ms": _percentile(ordered, 0.95),
        "p99_ms": _percentile(ordered, 0.99),
        "required_tiers": [{**tier, "status": "not_run"} for tier in LOAD_TIERS],
    }


def _percentile(samples: list[float], ratio: float) -> float | None:
    if not samples:
        return None
    index = min(len(samples) - 1, int(len(samples) * ratio))
    return round(samples[index] * 1000, 3)
