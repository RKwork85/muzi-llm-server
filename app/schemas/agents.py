"""Agent API 的公开响应。不包含思维链、工具参数或内部异常。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_serializer

from app.agents.models import Artifact, TaskStatus, UsageSnapshot


class AgentTaskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    request_id: str
    status: TaskStatus
    answer: str | None = None
    steps: int = 0
    tool_calls: int = 0
    error_code: str | None = None
    usage: UsageSnapshot | None = None
    artifacts: list[Artifact] | None = None

    @model_serializer(mode="wrap")
    def _hide_empty_artifacts(self, handler, info):
        data = handler(self)
        if not data.get("artifacts"):
            data.pop("artifacts", None)
        return data
