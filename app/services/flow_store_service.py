"""AI 画布拆分存储：应用、会话、画布、节点、连线、上传图。

设计要点：
- 应用与会话一条一行，避免「一份列表文档覆盖写」把别的设备创建的数据抹掉；
- 画布不再整份 JSONB 覆盖：节点 / 连线各存一行，写入只重写变化的行，
  ``revision`` 既做乐观锁，也做「这行最后在第几版变过」的游标，读端可带 ``since`` 拿增量；
- 上传图只存 OSS 地址与 sha256，引用关系落在 ``flow_image_refs``，
  ``ref_count`` 由引用行汇总，归零后交给后台任务回收。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import FlowRevisionConflictError, InvalidRequestError
from app.data.oss_client import OssUploadClient, OssUploadError
from app.models.flow import (
    FlowApp,
    FlowCanvas,
    FlowCanvasEdge,
    FlowCanvasNode,
    FlowImage,
    FlowImageRef,
    FlowOfficialWorkflow,
    FlowSession,
)
from app.models.mixins import utcnow
from app.services.flow_service import externalize_data_urls

logger = logging.getLogger("muzi_llm")

_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_MAX_NODES = 400
_MAX_EDGES = 1200
_MAX_CANVAS_BYTES = 2_000_000
_MAX_IMAGE_BYTES = 10 * 1024 * 1024


def _key(value: str, label: str) -> str:
    text = (value or "").strip()
    if not _KEY.match(text):
        raise InvalidRequestError(f"{label} 无效")
    return text


def _clean_tags(tags: list[str] | None) -> list[str]:
    if not tags:
        return []
    cleaned = [str(tag).strip() for tag in tags if str(tag).strip()]
    return cleaned[:8]


def _item_id(item: Any, label: str) -> str:
    if not isinstance(item, dict):
        raise InvalidRequestError(f"{label}必须是对象")
    ident = str(item.get("id") or "").strip()
    if not _KEY.match(ident):
        raise InvalidRequestError(f"{label} id 无效")
    return ident


def _collect_image_urls(payload: Any) -> set[str]:
    """从节点 payload 里收集上传图的公网地址（只认 upload 节点的 data.src）。"""
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "src" and isinstance(item, str) and item.startswith(("http://", "https://")):
                    found.add(item)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return found


@dataclass(slots=True)
class CanvasSnapshot:
    """组装好的会话画布：接口层只认这个结构，不关心底下拆了几张表。"""

    revision: int
    viewport: dict[str, Any]
    nodes: list[Any] = field(default_factory=list)
    edges: list[Any] = field(default_factory=list)
    updated_at: datetime | None = None
    node_count: int = 0
    edge_count: int = 0

    def as_bytes(self) -> bytes:
        return json.dumps(
            {"viewport": self.viewport, "nodes": self.nodes, "edges": self.edges},
            ensure_ascii=False,
        ).encode("utf-8")


@dataclass(slots=True)
class CanvasDelta:
    """带 ``since`` 读出来的增量：新增/更新的条目与墓碑 id 分开给。"""

    revision: int
    since_revision: int
    viewport: dict[str, Any]
    node_upserts: list[Any] = field(default_factory=list)
    node_deletes: list[str] = field(default_factory=list)
    edge_upserts: list[Any] = field(default_factory=list)
    edge_deletes: list[str] = field(default_factory=list)
    updated_at: datetime | None = None


class FlowStoreService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # 应用
    # ------------------------------------------------------------------

    async def list_apps(self, user_id: Any, workspace_key: str | None = None) -> list[FlowApp]:
        stmt = select(FlowApp).where(FlowApp.user_id == user_id, FlowApp.deleted_at.is_(None))
        if workspace_key:
            stmt = stmt.where(FlowApp.workspace_key == _key(workspace_key, "板块 key"))
        stmt = stmt.order_by(FlowApp.updated_at.desc())
        return list(await self._session.scalars(stmt))

    async def upsert_app(self, user_id: Any, data: dict[str, Any]) -> FlowApp:
        workspace_key = _key(str(data.get("workspace_key") or ""), "板块 key")
        app_key = _key(str(data.get("app_key") or ""), "应用 key")
        row = await self._session.scalar(
            select(FlowApp).where(
                FlowApp.user_id == user_id,
                FlowApp.workspace_key == workspace_key,
                FlowApp.app_key == app_key,
            )
        )
        if row is None:
            row = FlowApp(
                user_id=user_id,
                workspace_key=workspace_key,
                app_key=app_key,
                name="",
            )
            self._session.add(row)
        row.name = str(data.get("name") or "").strip()[:200] or "未命名工作流应用"
        row.icon = str(data.get("icon") or "✧")[:16]
        row.accent = str(data.get("accent") or "#6366f1")[:32]
        row.description = str(data.get("description") or "")[:2000]
        row.tags = _clean_tags(data.get("tags"))
        row.demo = str(data.get("demo") or "blank")[:64]
        row.origin = str(data.get("origin") or "user")[:32]
        row.source_key = str(data.get("source_key") or "")[:64]
        row.deleted_at = None
        await self._session.flush()
        return row

    async def delete_app(self, user_id: Any, workspace_key: str, app_key: str) -> bool:
        workspace = _key(workspace_key, "板块 key")
        app = _key(app_key, "应用 key")
        row = await self._session.scalar(
            select(FlowApp).where(
                FlowApp.user_id == user_id,
                FlowApp.workspace_key == workspace,
                FlowApp.app_key == app,
                FlowApp.deleted_at.is_(None),
            )
        )
        if row is None:
            return False
        now = utcnow()
        row.deleted_at = now
        # 应用删掉后，它的会话也没有入口了；保留画布行，便于排查与后续恢复。
        sessions = await self._session.scalars(
            select(FlowSession).where(
                FlowSession.user_id == user_id,
                FlowSession.workspace_key == workspace,
                FlowSession.workflow_key == app,
                FlowSession.deleted_at.is_(None),
            )
        )
        for item in sessions:
            item.deleted_at = now
            await self._release_canvas_image_refs(await self._load_canvas(item.id))
        await self._session.flush()
        return True

    # ------------------------------------------------------------------
    # 会话
    # ------------------------------------------------------------------

    async def list_sessions(
        self,
        user_id: Any,
        workspace_key: str | None = None,
        workflow_key: str | None = None,
    ) -> list[FlowSession]:
        stmt = select(FlowSession).where(
            FlowSession.user_id == user_id, FlowSession.deleted_at.is_(None)
        )
        if workspace_key:
            stmt = stmt.where(FlowSession.workspace_key == _key(workspace_key, "板块 key"))
        if workflow_key:
            stmt = stmt.where(FlowSession.workflow_key == _key(workflow_key, "工作流 key"))
        stmt = stmt.order_by(FlowSession.updated_at.desc())
        return list(await self._session.scalars(stmt))

    async def upsert_session(
        self,
        user_id: Any,
        workspace_key: str,
        workflow_key: str,
        session_key: str,
        name: str = "",
    ) -> FlowSession:
        row = await self._get_session(user_id, workspace_key, workflow_key, session_key, create=True)
        assert row is not None
        clean = str(name or "").strip()[:200]
        if clean:
            row.name = clean
        row.updated_at = utcnow()
        await self._session.flush()
        return row

    async def rename_session(
        self,
        user_id: Any,
        workspace_key: str,
        workflow_key: str,
        session_key: str,
        name: str,
    ) -> FlowSession | None:
        row = await self._get_session(user_id, workspace_key, workflow_key, session_key)
        if row is None:
            return None
        clean = str(name or "").strip()[:200]
        if clean:
            row.name = clean
        row.updated_at = utcnow()
        await self._session.flush()
        return row

    async def delete_session(
        self, user_id: Any, workspace_key: str, workflow_key: str, session_key: str
    ) -> bool:
        row = await self._get_session(user_id, workspace_key, workflow_key, session_key)
        if row is None:
            return False
        row.deleted_at = utcnow()
        await self._release_canvas_image_refs(await self._load_canvas(row.id))
        await self._session.flush()
        return True

    async def _get_session(
        self,
        user_id: Any,
        workspace_key: str,
        workflow_key: str,
        session_key: str,
        *,
        create: bool = False,
        name: str = "",
    ) -> FlowSession | None:
        workspace = _key(workspace_key, "板块 key")
        workflow = _key(workflow_key, "工作流 key")
        session = _key(session_key, "会话 key")
        row = await self._session.scalar(
            select(FlowSession).where(
                FlowSession.user_id == user_id,
                FlowSession.workspace_key == workspace,
                FlowSession.workflow_key == workflow,
                FlowSession.session_key == session,
            )
        )
        if row is None and create:
            row = FlowSession(
                user_id=user_id,
                workspace_key=workspace,
                workflow_key=workflow,
                session_key=session,
                name=str(name or "").strip()[:200],
            )
            self._session.add(row)
            await self._session.flush()
            return row
        if row is not None and row.deleted_at is not None:
            if not create:
                return None
            row.deleted_at = None
        return row

    # ------------------------------------------------------------------
    # 画布：节点 / 连线按行存
    # ------------------------------------------------------------------

    async def _load_canvas(self, session_id: Any) -> FlowCanvas | None:
        if session_id is None:
            return None
        return await self._session.scalar(
            select(FlowCanvas).where(FlowCanvas.session_id == session_id)
        )

    async def _rows(
        self, model: Any, canvas_id: Any, *, include_deleted: bool = False, since: int = 0
    ) -> list[Any]:
        stmt = select(model).where(model.canvas_id == canvas_id)
        if not include_deleted:
            stmt = stmt.where(model.deleted_at.is_(None))
        if since:
            stmt = stmt.where(model.revision > since)
        stmt = stmt.order_by(model.sort_index, model.created_at)
        return list(await self._session.scalars(stmt))

    async def get_canvas(
        self, user_id: Any, workspace_key: str, workflow_key: str, session_key: str
    ) -> CanvasSnapshot | None:
        session_row = await self._get_session(user_id, workspace_key, workflow_key, session_key)
        if session_row is None:
            return None
        canvas = await self._load_canvas(session_row.id)
        if canvas is None:
            return None
        await self._materialize_legacy(canvas)
        node_rows = await self._rows(FlowCanvasNode, canvas.id)
        edge_rows = await self._rows(FlowCanvasEdge, canvas.id)
        return CanvasSnapshot(
            revision=int(canvas.revision or 1),
            viewport=dict(canvas.viewport or {}),
            nodes=[row.payload for row in node_rows],
            edges=[row.payload for row in edge_rows],
            updated_at=canvas.updated_at,
            node_count=len(node_rows),
            edge_count=len(edge_rows),
        )

    async def get_canvas_delta(
        self,
        user_id: Any,
        workspace_key: str,
        workflow_key: str,
        session_key: str,
        since: int,
    ) -> CanvasDelta | None:
        """返回 ``since`` 之后变过的条目；调用方负责判断 since 是否仍可增量。"""
        session_row = await self._get_session(user_id, workspace_key, workflow_key, session_key)
        if session_row is None:
            return None
        canvas = await self._load_canvas(session_row.id)
        if canvas is None:
            return None
        await self._materialize_legacy(canvas)
        node_rows = await self._rows(FlowCanvasNode, canvas.id, include_deleted=True, since=since)
        edge_rows = await self._rows(FlowCanvasEdge, canvas.id, include_deleted=True, since=since)
        return CanvasDelta(
            revision=int(canvas.revision or 1),
            since_revision=since,
            viewport=dict(canvas.viewport or {}),
            node_upserts=[row.payload for row in node_rows if row.deleted_at is None],
            node_deletes=[row.node_id for row in node_rows if row.deleted_at is not None],
            edge_upserts=[row.payload for row in edge_rows if row.deleted_at is None],
            edge_deletes=[row.edge_id for row in edge_rows if row.deleted_at is not None],
            updated_at=canvas.updated_at,
        )

    async def _materialize_legacy(self, canvas: FlowCanvas) -> None:
        """老画布只有整份 JSONB：第一次读到时拆成行，之后快照列清空不再使用。"""
        legacy_nodes = list(canvas.nodes or [])
        legacy_edges = list(canvas.edges or [])
        if not legacy_nodes and not legacy_edges:
            return
        revision = int(canvas.revision or 1)
        existing_nodes = {
            row.node_id
            for row in await self._session.scalars(
                select(FlowCanvasNode).where(FlowCanvasNode.canvas_id == canvas.id)
            )
        }
        existing_edges = {
            row.edge_id
            for row in await self._session.scalars(
                select(FlowCanvasEdge).where(FlowCanvasEdge.canvas_id == canvas.id)
            )
        }
        for index, item in enumerate(legacy_nodes):
            try:
                ident = _item_id(item, "节点")
            except InvalidRequestError:
                continue
            if ident in existing_nodes:
                continue
            self._session.add(
                FlowCanvasNode(
                    canvas_id=canvas.id,
                    node_id=ident,
                    payload=item,
                    sort_index=index,
                    revision=revision,
                )
            )
        for index, item in enumerate(legacy_edges):
            try:
                ident = _item_id(item, "连线")
            except InvalidRequestError:
                continue
            if ident in existing_edges:
                continue
            self._session.add(
                FlowCanvasEdge(
                    canvas_id=canvas.id,
                    edge_id=ident,
                    payload=item,
                    sort_index=index,
                    revision=revision,
                )
            )
        canvas.nodes = []
        canvas.edges = []
        await self._session.flush()
        canvas.node_count = int(
            await self._session.scalar(
                select(func.count(FlowCanvasNode.id)).where(
                    FlowCanvasNode.canvas_id == canvas.id, FlowCanvasNode.deleted_at.is_(None)
                )
            )
            or 0
        )
        canvas.edge_count = int(
            await self._session.scalar(
                select(func.count(FlowCanvasEdge.id)).where(
                    FlowCanvasEdge.canvas_id == canvas.id, FlowCanvasEdge.deleted_at.is_(None)
                )
            )
            or 0
        )

    async def put_canvas(
        self,
        user_id: Any,
        workspace_key: str,
        workflow_key: str,
        session_key: str,
        *,
        base_revision: int,
        viewport: dict[str, Any] | None = None,
        nodes: list[Any] | None = None,
        edges: list[Any] | None = None,
        node_upserts: list[Any] | None = None,
        node_deletes: list[str] | None = None,
        edge_upserts: list[Any] | None = None,
        edge_deletes: list[str] | None = None,
        name: str = "",
        override: bool = False,
        oss: OssUploadClient | None = None,
    ) -> CanvasSnapshot:
        """写入画布。

        两种模式：
        - 全量：给 ``nodes`` + ``edges``，适合第一次落库或旧客户端；
        - 增量：给 ``*_upserts`` / ``*_deletes``，服务端只改变化的行。
        ``override=True`` 用于冲突后「以我为基准」，跳过 base_revision 校验。
        """
        full = nodes is not None or edges is not None
        patch = any(
            value is not None
            for value in (node_upserts, node_deletes, edge_upserts, edge_deletes)
        )
        if full and patch:
            raise InvalidRequestError("全量与增量不能同时提交")
        if full and (nodes is None or edges is None):
            raise InvalidRequestError("全量写入必须同时提供 nodes 与 edges")
        if not full and not patch:
            raise InvalidRequestError("没有可写入的画布内容")
        if viewport is not None and not isinstance(viewport, dict):
            raise InvalidRequestError("画布 viewport 必须是对象")

        session_row = await self._get_session(
            user_id, workspace_key, workflow_key, session_key, create=True, name=name
        )
        assert session_row is not None
        clean_name = str(name or "").strip()[:200]
        if clean_name and not session_row.name:
            session_row.name = clean_name

        canvas = await self._load_canvas(session_row.id)
        created = canvas is None
        if created:
            if base_revision > 0 and not override:
                raise FlowRevisionConflictError()
            canvas = FlowCanvas(user_id=user_id, session_id=session_row.id, revision=1)
            self._session.add(canvas)
        else:
            if not override and int(base_revision) != int(canvas.revision):
                raise FlowRevisionConflictError()
        assert canvas is not None
        await self._session.flush()

        await self._materialize_legacy(canvas)
        node_rows = await self._rows(FlowCanvasNode, canvas.id)
        edge_rows = await self._rows(FlowCanvasEdge, canvas.id)
        node_by_id = {row.node_id: row for row in node_rows}
        edge_by_id = {row.edge_id: row for row in edge_rows}
        current_nodes = [row.payload for row in node_rows]
        current_edges = [row.payload for row in edge_rows]
        before_urls = _collect_image_urls(current_nodes)

        revision = 1 if created else int(canvas.revision or 1) + 1

        if full:
            next_nodes = self._clean_items(nodes or [], _MAX_NODES, "画布节点过多")
            next_edges = self._clean_items(edges or [], _MAX_EDGES, "画布连线过多")
        else:
            next_nodes = self._apply_patch(
                current_nodes, node_upserts or [], node_deletes or [], _MAX_NODES, "画布节点过多"
            )
            next_edges = self._apply_patch(
                current_edges, edge_upserts or [], edge_deletes or [], _MAX_EDGES, "画布连线过多"
            )

        stored_viewport = (
            json.loads(json.dumps(viewport))
            if viewport is not None
            else dict(canvas.viewport or {})
        )

        # 只给「内容真的变了」的节点补 data URL 转存，避免每次保存都重扫全画布。
        node_ids = [str(item.get("id")) for item in next_nodes]
        changed = {
            ident
            for ident, item in zip(node_ids, next_nodes)
            if node_by_id.get(ident) is None or node_by_id[ident].payload != item
        }
        for index, ident in enumerate(node_ids):
            if ident not in changed:
                continue
            payload = json.loads(json.dumps(next_nodes[index]))
            await externalize_data_urls(payload, oss, str(user_id), _key(session_key, "会话 key"))
            next_nodes[index] = payload

        edge_ids = [str(item.get("id")) for item in next_edges]
        changed_edges = {
            ident
            for ident, item in zip(edge_ids, next_edges)
            if edge_by_id.get(ident) is None or edge_by_id[ident].payload != item
        }

        snapshot = CanvasSnapshot(
            revision=revision,
            viewport=stored_viewport,
            nodes=next_nodes,
            edges=next_edges,
            node_count=len(next_nodes),
            edge_count=len(next_edges),
        )
        encoded = snapshot.as_bytes()
        if len(encoded) > _MAX_CANVAS_BYTES:
            raise InvalidRequestError("画布数据过大")

        now = utcnow()
        await self._write_rows(
            FlowCanvasNode,
            FlowCanvasNode.node_id,
            canvas.id,
            node_by_id,
            next_nodes,
            revision,
            changed,
            now,
        )
        await self._write_rows(
            FlowCanvasEdge,
            FlowCanvasEdge.edge_id,
            canvas.id,
            edge_by_id,
            next_edges,
            revision,
            changed_edges,
            now,
        )

        canvas.viewport = stored_viewport
        canvas.revision = revision
        canvas.node_count = len(next_nodes)
        canvas.edge_count = len(next_edges)
        canvas.bytes = len(encoded)
        canvas.updated_at = now
        session_row.updated_at = now

        await self._replace_canvas_image_refs(user_id, canvas, next_nodes, before_urls)
        await self._session.flush()
        logger.info(
            "flow canvas saved session=%s revision=%s mode=%s nodes=%s edges=%s",
            session_key,
            revision,
            "full" if full else "patch",
            len(next_nodes),
            len(next_edges),
        )
        return snapshot

    @staticmethod
    def _clean_items(items: Iterable[Any], limit: int, message: str) -> list[Any]:
        cleaned: list[Any] = []
        seen: set[str] = set()
        for item in items:
            ident = _item_id(item, "画布条目")
            if ident in seen:
                raise InvalidRequestError(f"画布条目 id 重复：{ident}")
            seen.add(ident)
            cleaned.append(item)
        if len(cleaned) > limit:
            raise InvalidRequestError(message)
        return cleaned

    @staticmethod
    def _apply_patch(
        current: list[Any],
        upserts: list[Any],
        deletes: list[str],
        limit: int,
        message: str,
    ) -> list[Any]:
        by_id = {str(item.get("id")): item for item in current}
        order = [str(item.get("id")) for item in current]
        for item in upserts:
            ident = _item_id(item, "画布条目")
            if ident not in by_id:
                order.append(ident)
            by_id[ident] = item
        for raw in deletes:
            ident = str(raw or "").strip()
            if not _KEY.match(ident):
                continue
            by_id.pop(ident, None)
            if ident in order:
                order.remove(ident)
        result = [by_id[ident] for ident in order if ident in by_id]
        if len(result) > limit:
            raise InvalidRequestError(message)
        return result

    async def _write_rows(
        self,
        model: Any,
        id_column: Any,
        canvas_id: Any,
        existing: dict[str, Any],
        items: list[Any],
        revision: int,
        changed: set[str],
        now: datetime,
    ) -> None:
        kept: set[str] = set()
        for index, item in enumerate(items):
            ident = str(item.get("id"))
            kept.add(ident)
            row = existing.get(ident)
            if row is None:
                self._session.add(
                    model(
                        canvas_id=canvas_id,
                        **{id_column.key: ident},
                        payload=item,
                        sort_index=index,
                        revision=revision,
                    )
                )
                continue
            if ident in changed:
                row.payload = item
                row.revision = revision
            row.deleted_at = None
            if row.sort_index != index:
                # 顺序变化不单独抬 revision：客户端只关心内容是否要刷新
                row.sort_index = index
        for ident, row in existing.items():
            if ident in kept:
                continue
            row.deleted_at = now
            row.revision = revision
            # 墓碑只要 id：内容留着没有意义，还会白占空间
            row.payload = {}

    # ------------------------------------------------------------------
    # 上传图与引用计数
    # ------------------------------------------------------------------

    async def _replace_canvas_image_refs(
        self,
        user_id: Any,
        canvas: FlowCanvas,
        nodes: list[Any],
        before_urls: set[str],
    ) -> None:
        """按当前节点重建这张画布的引用行，并顺带刷新相关图片的计数。"""
        await self._session.execute(
            delete(FlowImageRef).where(FlowImageRef.canvas_id == canvas.id)
        )
        per_node: list[tuple[str, set[str]]] = []
        after_urls: set[str] = set()
        for item in nodes:
            urls = _collect_image_urls(item)
            if not urls:
                continue
            after_urls |= urls
            per_node.append((str(item.get("id")), urls))

        affected = before_urls | after_urls
        if not affected:
            return
        images = {
            row.url: row.id
            for row in await self._session.scalars(
                select(FlowImage).where(
                    FlowImage.user_id == user_id, FlowImage.url.in_(affected)
                )
            )
        }
        for node_id, urls in per_node:
            for url in urls:
                image_id = images.get(url)
                if image_id is None:
                    continue
                self._session.add(
                    FlowImageRef(image_id=image_id, canvas_id=canvas.id, node_id=node_id)
                )
        await self._refresh_image_counts(user_id, affected)

    async def _release_canvas_image_refs(self, canvas: FlowCanvas | None) -> None:
        """会话 / 应用被删时，把这第画布上的引用一起释放，计数归零。"""
        if canvas is None:
            return
        await self._materialize_legacy(canvas)
        rows = await self._rows(FlowCanvasNode, canvas.id)
        urls = _collect_image_urls([row.payload for row in rows])
        await self._session.execute(
            delete(FlowImageRef).where(FlowImageRef.canvas_id == canvas.id)
        )
        if urls:
            await self._refresh_image_counts(canvas.user_id, urls)

    async def _refresh_image_counts(self, user_id: Any, urls: set[str]) -> None:
        if not urls:
            return
        now = utcnow()
        count_sq = (
            select(func.count(FlowImageRef.id))
            .where(FlowImageRef.image_id == FlowImage.id)
            .scalar_subquery()
        )
        await self._session.execute(
            update(FlowImage)
            .where(FlowImage.user_id == user_id, FlowImage.url.in_(urls))
            .values(ref_count=count_sq, last_seen_at=now)
        )
        # 上一条给 ref_count 赋的是子查询结果，第二次读到的才是新值
        await self._session.execute(
            update(FlowImage)
            .where(FlowImage.user_id == user_id, FlowImage.url.in_(urls))
            .values(
                orphaned_at=case(
                    (FlowImage.ref_count > 0, None),
                    else_=func.coalesce(FlowImage.orphaned_at, now),
                )
            )
        )

    async def recount_image_refs(self) -> int:
        """全量对账：把 ref_count 拉回真实引用数，修掉级联删除等造成的漂移。"""
        count_sq = (
            select(func.count(FlowImageRef.id))
            .where(FlowImageRef.image_id == FlowImage.id)
            .scalar_subquery()
        )
        result = await self._session.execute(
            update(FlowImage)
            .where(FlowImage.ref_count != count_sq)
            .values(ref_count=count_sq)
        )
        now = utcnow()
        await self._session.execute(
            update(FlowImage)
            .where(FlowImage.ref_count <= 0, FlowImage.orphaned_at.is_(None))
            .values(orphaned_at=now)
        )
        return int(result.rowcount or 0)

    async def purge_orphan_images(
        self,
        *,
        older_than_seconds: int,
        oss: OssUploadClient | None,
        limit: int = 200,
        user_id: Any = None,
    ) -> int:
        """删掉「没人引用且超过宽限期」的图：先删行（原子抢占），再删 OSS 对象。"""
        cutoff = utcnow() - timedelta(seconds=max(0, older_than_seconds))
        stmt = (
            select(FlowImage)
            .where(FlowImage.ref_count <= 0, FlowImage.created_at < cutoff)
            .order_by(FlowImage.created_at)
            .limit(limit)
        )
        if user_id is not None:
            stmt = stmt.where(FlowImage.user_id == user_id)
        rows = list(await self._session.scalars(stmt))
        removed = 0
        for row in rows:
            result = await self._session.execute(
                delete(FlowImage).where(
                    FlowImage.id == row.id,
                    FlowImage.ref_count <= 0,
                )
            )
            if not result.rowcount:
                continue
            removed += 1
            # 行已经删掉，OSS 删失败只会留下一个无名对象，不会误删在用的图。
            if oss is not None:
                try:
                    await oss.delete_url(row.url)
                except OssUploadError as exc:
                    logger.warning("flow orphan image delete failed url=%s error=%s", row.url, exc.code)
        return removed

    async def save_image(
        self,
        user_id: Any,
        workspace_key: str,
        workflow_key: str,
        session_key: str,
        node_id: str,
        content: bytes,
        mime: str,
        oss: OssUploadClient | None,
    ) -> FlowImage:
        if not content:
            raise InvalidRequestError("上传图不能为空")
        if len(content) > _MAX_IMAGE_BYTES:
            raise InvalidRequestError("上传图超过 10MB")
        media = (mime or "image/png").lower()
        if media == "image/jpg":
            media = "image/jpeg"
        if media not in _MIME_EXT:
            raise InvalidRequestError("只支持 png / jpeg / webp / gif")

        digest = hashlib.sha256(content).hexdigest()
        existing = await self._session.scalar(
            select(FlowImage).where(FlowImage.user_id == user_id, FlowImage.sha256 == digest)
        )
        if existing is not None:
            existing.last_seen_at = utcnow()
            return existing

        if oss is None:
            raise InvalidRequestError("对象存储未配置，上传图暂时无法保存")
        try:
            uploaded = await oss.upload_files(
                [(f"flow_{digest[:12]}{_MIME_EXT[media]}", content)],
                user_id=str(user_id),
                category="image",
                scope=_key(session_key, "会话 key"),
                content_types=[media],
            )
        except OssUploadError as exc:
            logger.warning("flow image upload failed error=%s", exc.code)
            raise InvalidRequestError("上传图保存失败，请稍后重试") from exc
        if not uploaded:
            raise InvalidRequestError("上传图保存失败，请稍后重试")

        session = await self._get_session(
            user_id, workspace_key, workflow_key, session_key, create=True
        )
        row = FlowImage(
            user_id=user_id,
            session_id=session.id if session else None,
            node_id=str(node_id or "")[:64],
            sha256=digest,
            url=uploaded[0],
            mime=media,
            bytes=len(content),
            last_seen_at=utcnow(),
        )
        self._session.add(row)
        await self._session.flush()
        return row

    # ------------------------------------------------------------------
    # 官方工作流案例（平台级，超管管理）
    # ------------------------------------------------------------------

    async def list_official_workflows(
        self, workspace_key: str | None = None
    ) -> list[FlowOfficialWorkflow]:
        stmt = select(FlowOfficialWorkflow).where(
            FlowOfficialWorkflow.deleted_at.is_(None)
        )
        if workspace_key:
            stmt = stmt.where(FlowOfficialWorkflow.workspace_key == _key(workspace_key, "板块 key"))
        stmt = stmt.order_by(FlowOfficialWorkflow.sort_order.asc(), FlowOfficialWorkflow.created_at.asc())
        return list(await self._session.scalars(stmt))

    async def upsert_official_workflow(self, data: dict[str, Any]) -> FlowOfficialWorkflow:
        workspace_key = _key(str(data.get("workspace_key") or ""), "板块 key")
        workflow_key = _key(str(data.get("workflow_key") or ""), "案例 key")
        row = await self._session.scalar(
            select(FlowOfficialWorkflow).where(
                FlowOfficialWorkflow.workspace_key == workspace_key,
                FlowOfficialWorkflow.workflow_key == workflow_key,
            )
        )
        if row is None:
            row = FlowOfficialWorkflow(
                workspace_key=workspace_key,
                workflow_key=workflow_key,
                name="",
            )
            self._session.add(row)
            # 新建且未显式指定排序：排到当前板块末尾，避免和内置案例的 sort_order 冲突。
            if not isinstance(data.get("sort_order"), int):
                max_order = await self._session.scalar(
                    select(func.max(FlowOfficialWorkflow.sort_order)).where(
                        FlowOfficialWorkflow.workspace_key == workspace_key,
                        FlowOfficialWorkflow.deleted_at.is_(None),
                    )
                )
                row.sort_order = (max_order if max_order is not None else -1) + 1
        row.name = str(data.get("name") or "").strip()[:200] or "未命名官方案例"
        row.icon = str(data.get("icon") or "✧")[:16]
        row.accent = str(data.get("accent") or "#6366f1")[:32]
        row.description = str(data.get("description") or "")[:2000]
        row.tags = _clean_tags(data.get("tags"))
        row.status = str(data.get("status") or "ready")[:16] or "ready"
        row.demo = str(data.get("demo") or "blank")[:64]
        if isinstance(data.get("sort_order"), int):
            row.sort_order = max(0, data["sort_order"])
        snapshot = data.get("snapshot")
        if isinstance(snapshot, dict):
            row.snapshot = snapshot
        elif row.snapshot is None:
            row.snapshot = {}
        row.deleted_at = None
        await self._session.flush()
        return row

    async def delete_official_workflow(self, workspace_key: str, workflow_key: str) -> bool:
        workspace = _key(workspace_key, "板块 key")
        workflow = _key(workflow_key, "案例 key")
        row = await self._session.scalar(
            select(FlowOfficialWorkflow).where(
                FlowOfficialWorkflow.workspace_key == workspace,
                FlowOfficialWorkflow.workflow_key == workflow,
                FlowOfficialWorkflow.deleted_at.is_(None),
            )
        )
        if row is None:
            return False
        row.deleted_at = utcnow()
        await self._session.flush()
        return True

    async def reorder_official_workflows(
        self, workspace_key: str, workflow_keys: list[str]
    ) -> int:
        workspace = _key(workspace_key, "板块 key")
        keys = [str(key).strip() for key in workflow_keys if str(key).strip()]
        if not keys:
            raise InvalidRequestError("排序列表不能为空")
        # 只更新本表已存在的案例：排序数组可能含前端静态内置案例的 key，
        # 它们不落本表，跳过即可。
        updated = 0
        for order, key in enumerate(keys):
            row = await self._session.scalar(
                select(FlowOfficialWorkflow).where(
                    FlowOfficialWorkflow.workspace_key == workspace,
                    FlowOfficialWorkflow.workflow_key == key,
                    FlowOfficialWorkflow.deleted_at.is_(None),
                )
            )
            if row is None or row.sort_order == order:
                continue
            row.sort_order = order
            updated += 1
        await self._session.flush()
        return updated
