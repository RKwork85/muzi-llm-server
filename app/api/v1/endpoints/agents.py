"""Agent 任务接口。客户端断开 SSE 只结束订阅，不取消运行。"""

from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.agents.models import AgentEventType, AgentRequest
from app.agents.runtime import AgentRuntime
from app.api.deps import get_agent_runtime
from app.core.context import get_request_id
from app.core.exceptions import AgentTaskNotFoundError
from app.core.sse import format_sse
from app.schemas.agents import AgentTaskResponse
from app.tools.workspace_scope import scope_dirname

router = APIRouter(prefix="/agents", tags=["agents"])

_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_TERMINAL_EVENTS = frozenset(
    {
        AgentEventType.COMPLETED,
        AgentEventType.FAILED,
        AgentEventType.CANCELLED,
        AgentEventType.LIMIT_REACHED,
    }
)


@router.post("/runs", response_model=AgentTaskResponse, summary="创建 Agent 运行")
async def create_run(
    body: AgentRequest,
    request: Request,
    runtime: AgentRuntime = Depends(get_agent_runtime),
) -> AgentTaskResponse:
    view = await runtime.start(
        body,
        get_request_id(),
        workspace_scope=scope_dirname(request.headers.get("x-workspace-key")),
    )
    return AgentTaskResponse.model_validate(view)


@router.get("/runs/{task_id}", response_model=AgentTaskResponse, summary="查询 Agent 任务")
async def read_run(
    task_id: str,
    runtime: AgentRuntime = Depends(get_agent_runtime),
) -> AgentTaskResponse:
    _known_task(task_id)
    return AgentTaskResponse.model_validate(runtime.get(task_id))


@router.post("/runs/{task_id}/cancel", response_model=AgentTaskResponse, summary="取消 Agent 运行")
async def cancel_run(
    task_id: str,
    runtime: AgentRuntime = Depends(get_agent_runtime),
) -> AgentTaskResponse:
    _known_task(task_id)
    view = await runtime.cancel(task_id)
    return AgentTaskResponse.model_validate(view)


@router.get("/runs/{task_id}/events", summary="订阅 Agent 事件")
async def stream_run(
    task_id: str,
    request: Request,
    runtime: AgentRuntime = Depends(get_agent_runtime),
) -> StreamingResponse:
    _known_task(task_id)
    publisher = runtime.publisher_for(task_id)
    return StreamingResponse(
        _frames(request, publisher),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _known_task(task_id: str) -> None:
    if not _TASK_ID.fullmatch(task_id):
        raise AgentTaskNotFoundError()


async def _frames(request: Request, publisher) -> object:
    queue = publisher.subscribe()
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
            except TimeoutError:
                yield ": heartbeat\n\n"
                continue
            yield format_sse(event.type.value, event.model_dump(mode="json"))
            if event.type in _TERMINAL_EVENTS:
                break
    finally:
        publisher.unsubscribe(queue)
