"""MCP 日志、计数和熔断。不记录令牌、Cookie 或参数正文。"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from contextlib import contextmanager
from typing import Iterator

logger = logging.getLogger("muzi_llm")
_FORBIDDEN = ("token", "secret", "cookie", "password", "authorization", "verifier", "code")


class Metrics:
    def __init__(self) -> None:
        self.counters: dict[str, int] = defaultdict(int)
        self.samples: dict[str, list[float]] = defaultdict(list)
        self.gauges: dict[str, float] = {}

    def add(self, name: str, value: int = 1) -> None:
        self.counters[name] += value

    def observe(self, name: str, seconds: float) -> None:
        self.samples[name].append(seconds)

    def bump(self, name: str, delta: float) -> None:
        self.gauges[name] = self.gauges.get(name, 0) + delta


metrics = Metrics()
audits: list[dict[str, str]] = []
breaker_states: dict[str, str] = {}


class Breaker:
    def __init__(self, *, limit: int = 5, reset_seconds: float = 30) -> None:
        self.limit = limit
        self.reset_seconds = reset_seconds
        self.failures = 0
        self.open_until = 0.0
        self.state = "closed"

    def allow(self) -> bool:
        now = time.monotonic()
        if self.state == "open":
            if now < self.open_until:
                _remember_breaker(self)
                return False
            self.state = "half_open"
            _remember_breaker(self)
            return True
        if self.state == "half_open":
            _remember_breaker(self)
            return False
        _remember_breaker(self)
        return True

    def success(self) -> None:
        self.failures = 0
        self.open_until = 0.0
        self.state = "closed"
        _remember_breaker(self)

    def failure(self) -> None:
        self.failures += 1
        if self.state == "half_open" or self.failures >= self.limit:
            self.state = "open"
            self.open_until = time.monotonic() + self.reset_seconds
        _remember_breaker(self)


_BREAKERS: dict[str, Breaker] = {}


def breaker_for(connector_id: str) -> Breaker:
    breaker = _BREAKERS.get(connector_id)
    if breaker is None:
        breaker = Breaker()
        breaker.connector_id = connector_id
        _BREAKERS[connector_id] = breaker
    _remember_breaker(breaker)
    return breaker


def _remember_breaker(breaker: Breaker) -> None:
    connector_id = getattr(breaker, "connector_id", "")
    if not connector_id:
        return
    breaker_states[connector_id] = breaker.state
    metrics.gauges["mcp_circuit_breaker_state"] = float(sum(item == "open" for item in breaker_states.values()))


def mcp_log(event: str, **fields: object) -> None:
    safe = redact({key: value for key, value in fields.items()})
    safe["event"] = event
    audits.append(safe)
    if len(audits) > 200:
        del audits[:100]
    logger.info("%s %s", event, " ".join(f"{key}={value}" for key, value in sorted(safe.items()) if key != "event"))


def clear_observe() -> None:
    metrics.counters.clear()
    metrics.samples.clear()
    metrics.gauges.clear()
    audits.clear()
    breaker_states.clear()
    _BREAKERS.clear()


def redact(fields: dict[str, object]) -> dict[str, str]:
    clean: dict[str, str] = {}
    for key, value in fields.items():
        lowered = key.lower()
        if any(word in lowered for word in _FORBIDDEN):
            continue
        text = "" if value is None else str(value)
        if any(word in text.lower() for word in ("bearer ", "cookie=")):
            text = "[redacted]"
        clean[key] = text[:120]
    return clean


@contextmanager
def observe_tool(connector_id: str) -> Iterator[None]:
    metrics.add("mcp_tool_calls_total")
    metrics.add("mcp_connection_total")
    started = time.perf_counter()
    try:
        yield
    except Exception:
        metrics.add("mcp_connection_failures_total")
        raise
    finally:
        metrics.observe("mcp_tool_call_duration", time.perf_counter() - started)
        del connector_id
