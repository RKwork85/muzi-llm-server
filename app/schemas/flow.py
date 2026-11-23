"""AI 画布文档的请求和响应。

两套契约并存：
- ``FlowDocument*``：旧版整份 JSON 文档（保留兼容读）；
- ``FlowApp*`` / ``FlowSession*`` / ``FlowCanvas*`` / ``FlowImage*``：拆分后的应用、会话、
  画布与上传图。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FlowDocumentSaveRequest(ApiContract):
    payload: dict[str, Any] = Field(default_factory=dict)


class FlowDocumentItem(ApiContract):
    doc_key: str
    kind: str
    payload: dict[str, Any]
    updated_at: datetime


class FlowDocumentListResponse(ApiContract):
    items: list[FlowDocumentItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 应用：我的空间里一条一行
# ---------------------------------------------------------------------------


class FlowAppSaveRequest(ApiContract):
    workspace_key: str = Field(min_length=1, max_length=64)
    app_key: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=200)
    icon: str = Field(default="✧", max_length=16)
    accent: str = Field(default="#6366f1", max_length=32)
    description: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=8)
    demo: str = Field(default="blank", max_length=64)
    origin: str = Field(default="user", max_length=32)
    source_key: str = Field(default="", max_length=64)


class FlowAppItem(ApiContract):
    workspace_key: str
    app_key: str
    name: str
    icon: str
    accent: str
    description: str
    tags: list[str]
    demo: str
    origin: str
    source_key: str
    created_at: datetime
    updated_at: datetime


class FlowAppListResponse(ApiContract):
    items: list[FlowAppItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 会话：名称与更新时间
# ---------------------------------------------------------------------------


class FlowSessionSaveRequest(ApiContract):
    workspace_key: str = Field(min_length=1, max_length=64)
    workflow_key: str = Field(min_length=1, max_length=64)
    session_key: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=200)


class FlowSessionRenameRequest(ApiContract):
    name: str = Field(max_length=200)


class FlowSessionItem(ApiContract):
    workspace_key: str
    workflow_key: str
    session_key: str
    name: str
    created_at: datetime
    updated_at: datetime


class FlowSessionListResponse(ApiContract):
    items: list[FlowSessionItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 画布：整份快照 + revision
# ---------------------------------------------------------------------------


class FlowCanvasSaveRequest(ApiContract):
    """画布写入。

    全量（``nodes`` + ``edges``）和增量（``*_upserts`` / ``*_deletes``）二选一：
    只改了几个节点时发增量，服务端只重写那几行，写入体积随改动量而不是画布大小增长。
    """

    base_revision: int = Field(default=0, ge=0)
    viewport: dict[str, Any] | None = None
    nodes: list[Any] | None = None
    edges: list[Any] | None = None
    node_upserts: list[Any] | None = None
    node_deletes: list[str] | None = None
    edge_upserts: list[Any] | None = None
    edge_deletes: list[str] | None = None
    # 冲突后「以我为基准」：跳过 base_revision 校验，强制写入
    override: bool = False
    # 首次写入时用来建会话行，之后改名走会话接口
    name: str = Field(default="", max_length=200)


class FlowCanvasItem(ApiContract):
    workspace_key: str
    workflow_key: str
    session_key: str
    revision: int
    viewport: dict[str, Any]
    nodes: list[Any]
    edges: list[Any]
    node_count: int = 0
    edge_count: int = 0
    updated_at: datetime | None = None


class FlowCanvasDeltaItem(ApiContract):
    """带 ``since`` 读出来的增量：只含变过的节点 / 连线与墓碑。"""

    workspace_key: str
    workflow_key: str
    session_key: str
    mode: str = "delta"
    since_revision: int
    revision: int
    viewport: dict[str, Any]
    node_upserts: list[Any] = Field(default_factory=list)
    node_deletes: list[str] = Field(default_factory=list)
    edge_upserts: list[Any] = Field(default_factory=list)
    edge_deletes: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# 上传图
# ---------------------------------------------------------------------------


class FlowImageItem(ApiContract):
    node_id: str
    sha256: str
    url: str
    mime: str
    bytes: int


# ---------------------------------------------------------------------------
# 官方工作流案例（超管管理）
# ---------------------------------------------------------------------------


class FlowOfficialWorkflowSaveRequest(ApiContract):
    workspace_key: str = Field(min_length=1, max_length=64)
    workflow_key: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=200)
    icon: str = Field(default="✧", max_length=16)
    accent: str = Field(default="#6366f1", max_length=32)
    description: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=8)
    status: str = Field(default="ready", max_length=16)
    demo: str = Field(default="blank", max_length=64)
    # 排序位置：未传（None）表示由后端自动排在末尾或保持原位置。
    sort_order: int | None = Field(default=None, ge=0)
    # 画布快照：{viewport, nodes, edges}；空白案例传空画布。
    snapshot: dict[str, Any] = Field(default_factory=dict)


class FlowOfficialWorkflowItem(ApiContract):
    workspace_key: str
    workflow_key: str
    name: str
    icon: str
    accent: str
    description: str
    tags: list[str]
    status: str
    demo: str
    sort_order: int
    snapshot: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class FlowOfficialWorkflowListResponse(ApiContract):
    items: list[FlowOfficialWorkflowItem] = Field(default_factory=list)


class FlowOfficialWorkflowReorderRequest(ApiContract):
    workspace_key: str = Field(min_length=1, max_length=64)
    # 排序后的 workflow_key 顺序，前端把「静态内置 + 动态」混合后的完整顺序传回来。
    workflow_keys: list[str] = Field(default_factory=list)
