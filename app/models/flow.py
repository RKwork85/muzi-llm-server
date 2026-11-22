"""AI 画布存储。

两套结构同时存在：
- ``FlowDocument``：早期「一份 JSON 文档」的写法，保留兼容读，新增数据不再写入；
- ``FlowApp`` / ``FlowSession`` / ``FlowCanvas`` / ``FlowCanvasNode`` /
  ``FlowCanvasEdge`` / ``FlowImage``：按应用、会话、画布、节点、连线、图片拆开的
  关系结构。

画布不再整份 JSONB 覆盖写：一个节点 / 一条连线一行，写入时只重写变化的行，
``revision`` 同时作为「这行最后在第几版被改过」的游标，读端可以按 ``since``
只拿增量。``FlowCanvas.nodes/edges`` 保留为旧版快照，仅用于迁移前的降级读。

图片二进制始终不进数据库。data URL 在写入前转到 muzi-oss，这里只留公网地址；
``ref_count`` 记录有多少画布节点还在引用它，归零后由后台任务回收。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.constants import sql_in
from app.models.mixins import CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin, utcnow

FLOW_DOCUMENT_KINDS = ("apps", "sessions", "canvas")


class FlowDocument(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "flow_documents"
    __table_args__ = (
        UniqueConstraint("user_id", "doc_key", name="uq_flow_documents_user_key"),
        CheckConstraint(sql_in("kind", FLOW_DOCUMENT_KINDS), name="ck_flow_documents_kind"),
        Index("ix_flow_documents_user_updated", "user_id", "updated_at"),
        {"comment": "AI 画布文档（旧版整份 JSON）：保留兼容读，新数据写拆分后的表。"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_flow_documents_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    doc_key: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )


class FlowApp(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """「我的空间」工作流应用元信息。一个应用一行，避免整份列表互相覆盖。"""

    __tablename__ = "flow_apps"
    __table_args__ = (
        UniqueConstraint("user_id", "workspace_key", "app_key", name="uq_flow_apps_user_app"),
        Index("ix_flow_apps_user_workspace", "user_id", "workspace_key", "updated_at"),
        {"comment": "我的空间工作流应用：一条一行，画布数据在 flow_canvases。"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_flow_apps_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_key: Mapped[str] = mapped_column(String(64), nullable=False)
    app_key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    icon: Mapped[str] = mapped_column(String(16), nullable=False, default="✧", server_default="✧")
    accent: Mapped[str] = mapped_column(
        String(32), nullable=False, default="#6366f1", server_default="#6366f1"
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    tags: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    demo: Mapped[str] = mapped_column(String(64), nullable=False, default="blank", server_default="blank")
    origin: Mapped[str] = mapped_column(String(32), nullable=False, default="user", server_default="user")
    source_key: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FlowSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """一条工作流会话。官方案例的会话同样落在这里，用 workflow_key 区分应用。"""

    __tablename__ = "flow_sessions"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "workspace_key",
            "workflow_key",
            "session_key",
            name="uq_flow_sessions_user_scope",
        ),
        Index(
            "ix_flow_sessions_user_workflow",
            "user_id",
            "workspace_key",
            "workflow_key",
            "updated_at",
        ),
        {"comment": "工作流会话元信息：名称与更新时间，画布在 flow_canvases。"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_flow_sessions_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_key: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_key: Mapped[str] = mapped_column(String(64), nullable=False)
    session_key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FlowCanvas(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """会话画布：视口 + 版本号。节点与连线在 flow_canvas_nodes / flow_canvas_edges。"""

    __tablename__ = "flow_canvases"
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_flow_canvases_session"),
        Index("ix_flow_canvases_user_updated", "user_id", "updated_at"),
        {"comment": "会话画布：viewport + revision；节点/连线各存一行。"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_flow_canvases_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("flow_sessions.id", name="fk_flow_canvases_session_id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    viewport: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # 旧版整份快照：迁移到行表后清空，只在降级回退时读写。
    nodes: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    edges: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    node_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    edge_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class FlowCanvasNode(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """画布节点：一个节点一行。

    ``revision`` 是「这一行最后在第几版被改过」，删除时只打 ``deleted_at`` 并保留
    墓碑，客户端带 ``since`` 拉取时能知道哪些节点没了。
    """

    __tablename__ = "flow_canvas_nodes"
    __table_args__ = (
        UniqueConstraint("canvas_id", "node_id", name="uq_flow_canvas_nodes_item"),
        Index("ix_flow_canvas_nodes_canvas_order", "canvas_id", "sort_index"),
        Index("ix_flow_canvas_nodes_canvas_rev", "canvas_id", "revision"),
        {"comment": "画布节点：一条一行，只重写改动的行；deleted_at 即墓碑。"},
    )

    canvas_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("flow_canvases.id", name="fk_flow_canvas_nodes_canvas_id", ondelete="CASCADE"),
        nullable=False,
    )
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    sort_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FlowCanvasEdge(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """画布连线：与节点同构，单独一张表，删除同样留墓碑。"""

    __tablename__ = "flow_canvas_edges"
    __table_args__ = (
        UniqueConstraint("canvas_id", "edge_id", name="uq_flow_canvas_edges_item"),
        Index("ix_flow_canvas_edges_canvas_order", "canvas_id", "sort_index"),
        Index("ix_flow_canvas_edges_canvas_rev", "canvas_id", "revision"),
        {"comment": "画布连线：一条一行，只重写改动的行；deleted_at 即墓碑。"},
    )

    canvas_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("flow_canvases.id", name="fk_flow_canvas_edges_canvas_id", ondelete="CASCADE"),
        nullable=False,
    )
    edge_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    sort_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FlowImage(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """画布上传图：只存 OSS 地址和内容哈希，同一张图在账号内复用同一份文件。"""

    __tablename__ = "flow_images"
    __table_args__ = (
        UniqueConstraint("user_id", "sha256", name="uq_flow_images_user_sha"),
        Index("ix_flow_images_session", "session_id"),
        Index("ix_flow_images_orphan", "user_id", "ref_count", "created_at"),
        {"comment": "画布上传图：OSS 地址 + sha256 + 引用计数；字节在 muzi-oss。"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_flow_images_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("flow_sessions.id", name="fk_flow_images_session_id", ondelete="SET NULL"),
    )
    node_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    mime: Mapped[str] = mapped_column(String(64), nullable=False, default="image/png")
    bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # 有多少个画布节点还在引用这张图；0 表示当前没人引用，交给后台回收。
    ref_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    orphaned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FlowImageRef(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """画布节点对上传图的引用。

    引用行是 ref_count 的唯一来源：节点删了行就没了，会话被删也能精确释放，
    不会出现「计数器减多了」这种没法自愈的漂移。
    """

    __tablename__ = "flow_image_refs"
    __table_args__ = (
        UniqueConstraint("image_id", "canvas_id", "node_id", name="uq_flow_image_refs_item"),
        Index("ix_flow_image_refs_canvas", "canvas_id"),
        Index("ix_flow_image_refs_image", "image_id"),
        {"comment": "画布节点 → 上传图的引用；ref_count 由它汇总。"},
    )

    image_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("flow_images.id", name="fk_flow_image_refs_image_id", ondelete="CASCADE"),
        nullable=False,
    )
    canvas_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "flow_canvases.id", name="fk_flow_image_refs_canvas_id", ondelete="CASCADE"
        ),
        nullable=False,
    )
    node_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
