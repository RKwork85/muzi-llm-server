"""统一 trace 标识派生。

三套运行时（ask / agent / plan / image）共享同一套 trace/span 语义：

- trace_id：一次用户意图的唯一标识，跨运行时稳定。
  ask 用 run_id，agent 用 task_id，plan 用 public_id，image 用 request_id。
- span_id：意图内一次具体调用（模型调用 / 工具 / 技能 / 摘要）的唯一标识。
- parent_span_id：父 span，例如工具调用挂在触发它的模型调用下。

本模块只做字符串派生，不查库、不结算。历史数据 trace_id 保持为空，
从新数据开始追溯。
"""

from __future__ import annotations

import uuid
from typing import Literal

Surface = Literal["ask", "agent", "plan", "image"]


def trace_id_for(
    surface: Surface,
    *,
    run_id: str | None = None,
    task_id: str | None = None,
    public_id: str | None = None,
    request_id: str | None = None,
) -> str | None:
    """按运行时派生 trace_id。取不到对应标识时返回 None，不伪造。"""
    if surface == "ask":
        return _clean(run_id)
    if surface == "agent":
        return _clean(task_id)
    if surface == "plan":
        return _clean(public_id)
    if surface == "image":
        return _clean(request_id)
    return None


def new_span_id() -> str:
    """生成一个 span 的唯一标识（UUID hex，32 字符）。"""
    return uuid.uuid4().hex


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:64] or None
