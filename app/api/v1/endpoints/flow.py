"""AI 画布接口：应用、会话、画布与上传图按当前用户读写。

``/flow/documents`` 是旧版「整份 JSON 文档」接口，保留兼容读；新前端走
``/flow/apps``、``/flow/sessions``、``/flow/sessions/{key}/canvas`` 和上传图接口。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile

from app.api.deps import get_authenticated_user
from app.authorization.dependencies import require_permission
from app.core.database.deps import DbSession
from app.data.oss_client import OssUploadClient
from app.models.flow import FlowApp, FlowSession
from app.models.tables import User
from app.schemas.flow import (
    FlowAppItem,
    FlowAppListResponse,
    FlowAppSaveRequest,
    FlowCanvasDeltaItem,
    FlowCanvasItem,
    FlowCanvasSaveRequest,
    FlowDocumentItem,
    FlowDocumentListResponse,
    FlowDocumentSaveRequest,
    FlowImageItem,
    FlowSessionItem,
    FlowSessionListResponse,
    FlowSessionRenameRequest,
    FlowSessionSaveRequest,
)
from app.services.flow_service import FlowService
from app.services.flow_store_service import CanvasSnapshot, FlowStoreService

router = APIRouter(prefix="/flow/documents", tags=["flow"])
store_router = APIRouter(prefix="/flow", tags=["flow"])
AuthenticatedUser = Annotated[User, Depends(get_authenticated_user)]
# 画布的写操作（保存/删除/上传）统一走这一个依赖；读接口不加，避免每次轮询都查权限。
FlowWriter = Annotated[User, Depends(require_permission("flow.write"))]


def _oss_client(request: Request) -> OssUploadClient | None:
    chat_service = getattr(request.app.state, "chat_service", None)
    return getattr(chat_service, "oss_client", None)


# ---------------------------------------------------------------------------
# 旧版整份文档（兼容读）
# ---------------------------------------------------------------------------


@router.get("", response_model=FlowDocumentListResponse, summary="列出当前用户的画布文档")
async def list_flow_documents(session: DbSession, user: AuthenticatedUser) -> FlowDocumentListResponse:
    rows = await FlowService(session).list_items(user.id)
    return FlowDocumentListResponse(
        items=[
            FlowDocumentItem(
                doc_key=row.doc_key,
                kind=row.kind,
                payload=row.payload,
                updated_at=row.updated_at,
            )
            for row in rows
        ]
    )


@router.put("/{doc_key:path}", response_model=FlowDocumentItem, summary="保存一份画布文档")
async def save_flow_document(
    doc_key: str,
    body: FlowDocumentSaveRequest,
    request: Request,
    session: DbSession,
    user: FlowWriter,
) -> FlowDocumentItem:
    row = await FlowService(session).upsert(user.id, doc_key, body.payload, _oss_client(request))
    return FlowDocumentItem(
        doc_key=row.doc_key,
        kind=row.kind,
        payload=row.payload,
        updated_at=row.updated_at,
    )


@router.delete("/{doc_key:path}", summary="删除一份画布文档")
async def delete_flow_document(
    doc_key: str,
    session: DbSession,
    user: FlowWriter,
) -> dict[str, object]:
    deleted = await FlowService(session).delete(user.id, doc_key)
    return {"doc_key": doc_key, "deleted": deleted}


# ---------------------------------------------------------------------------
# 应用
# ---------------------------------------------------------------------------


def _app_item(row: FlowApp) -> FlowAppItem:    return FlowAppItem(
        workspace_key=row.workspace_key,
        app_key=row.app_key,
        name=row.name,
        icon=row.icon,
        accent=row.accent,
        description=row.description or "",
        tags=list(row.tags or []),
        demo=row.demo,
        origin=row.origin,
        source_key=row.source_key or "",
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@store_router.get("/apps", response_model=FlowAppListResponse, summary="列出我的空间应用")
async def list_flow_apps(
    session: DbSession,
    user: AuthenticatedUser,
    workspace_key: str | None = Query(default=None),
) -> FlowAppListResponse:
    rows = await FlowStoreService(session).list_apps(user.id, workspace_key)
    return FlowAppListResponse(items=[_app_item(row) for row in rows])


@store_router.put("/apps", response_model=FlowAppItem, summary="写入一个应用")
async def save_flow_app(
    body: FlowAppSaveRequest,
    session: DbSession,
    user: FlowWriter,
) -> FlowAppItem:
    row = await FlowStoreService(session).upsert_app(user.id, body.model_dump())
    return _app_item(row)


@store_router.delete("/apps/{app_key}", summary="删除一个应用及其会话")
async def delete_flow_app(
    app_key: str,
    session: DbSession,
    user: FlowWriter,
    workspace_key: str = Query(...),
) -> dict[str, object]:
    deleted = await FlowStoreService(session).delete_app(user.id, workspace_key, app_key)
    return {"app_key": app_key, "deleted": deleted}


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------


def _session_item(row: FlowSession) -> FlowSessionItem:
    return FlowSessionItem(
        workspace_key=row.workspace_key,
        workflow_key=row.workflow_key,
        session_key=row.session_key,
        name=row.name or "",
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@store_router.get("/sessions", response_model=FlowSessionListResponse, summary="列出会话")
async def list_flow_sessions(
    session: DbSession,
    user: AuthenticatedUser,
    workspace_key: str | None = Query(default=None),
    workflow_key: str | None = Query(default=None),
) -> FlowSessionListResponse:
    rows = await FlowStoreService(session).list_sessions(user.id, workspace_key, workflow_key)
    return FlowSessionListResponse(items=[_session_item(row) for row in rows])


@store_router.put("/sessions", response_model=FlowSessionItem, summary="写入一条会话")
async def save_flow_session(
    body: FlowSessionSaveRequest,
    session: DbSession,
    user: FlowWriter,
) -> FlowSessionItem:
    row = await FlowStoreService(session).upsert_session(
        user.id,
        body.workspace_key,
        body.workflow_key,
        body.session_key,
        body.name,
    )
    return _session_item(row)


@store_router.patch("/sessions/{session_key}", response_model=FlowSessionItem, summary="会话改名")
async def rename_flow_session(
    session_key: str,
    body: FlowSessionRenameRequest,
    session: DbSession,
    user: FlowWriter,
    workspace_key: str = Query(...),
    workflow_key: str = Query(...),
) -> FlowSessionItem:
    row = await FlowStoreService(session).rename_session(
        user.id, workspace_key, workflow_key, session_key, body.name
    )
    if row is None:
        # 会话不存在时直接建一条，保证地址里的会话 ID 始终有效
        row = await FlowStoreService(session).upsert_session(
            user.id, workspace_key, workflow_key, session_key, body.name
        )
    return _session_item(row)


@store_router.delete("/sessions/{session_key}", summary="删除一条会话")
async def delete_flow_session(
    session_key: str,
    session: DbSession,
    user: FlowWriter,
    workspace_key: str = Query(...),
    workflow_key: str = Query(...),
) -> dict[str, object]:
    deleted = await FlowStoreService(session).delete_session(
        user.id, workspace_key, workflow_key, session_key
    )
    return {"session_key": session_key, "deleted": deleted}


# ---------------------------------------------------------------------------
# 画布
# ---------------------------------------------------------------------------


def _canvas_item(
    snapshot: CanvasSnapshot | None,
    workspace_key: str,
    workflow_key: str,
    session_key: str,
) -> FlowCanvasItem:
    if snapshot is None:
        return FlowCanvasItem(
            workspace_key=workspace_key,
            workflow_key=workflow_key,
            session_key=session_key,
            revision=0,
            viewport={},
            nodes=[],
            edges=[],
            updated_at=None,
        )
    return FlowCanvasItem(
        workspace_key=workspace_key,
        workflow_key=workflow_key,
        session_key=session_key,
        revision=snapshot.revision,
        viewport=snapshot.viewport or {},
        nodes=list(snapshot.nodes or []),
        edges=list(snapshot.edges or []),
        node_count=snapshot.node_count,
        edge_count=snapshot.edge_count,
        updated_at=snapshot.updated_at,
    )


@store_router.get(
    "/sessions/{session_key}/canvas",
    response_model=FlowCanvasItem | FlowCanvasDeltaItem,
    summary="读取会话画布（可带 since 取增量）",
)
async def get_flow_canvas(
    session_key: str,
    session: DbSession,
    user: AuthenticatedUser,
    workspace_key: str = Query(...),
    workflow_key: str = Query(...),
    since: int = Query(default=0, ge=0),
) -> FlowCanvasItem | FlowCanvasDeltaItem:
    store = FlowStoreService(session)
    if since > 0:
        delta = await store.get_canvas_delta(
            user.id, workspace_key, workflow_key, session_key, since
        )
        if delta is not None and since <= delta.revision:
            return FlowCanvasDeltaItem(
                workspace_key=workspace_key,
                workflow_key=workflow_key,
                session_key=session_key,
                since_revision=since,
                revision=delta.revision,
                viewport=delta.viewport,
                node_upserts=delta.node_upserts,
                node_deletes=delta.node_deletes,
                edge_upserts=delta.edge_upserts,
                edge_deletes=delta.edge_deletes,
                updated_at=delta.updated_at,
            )
    snapshot = await store.get_canvas(user.id, workspace_key, workflow_key, session_key)
    return _canvas_item(snapshot, workspace_key, workflow_key, session_key)


@store_router.put(
    "/sessions/{session_key}/canvas",
    response_model=FlowCanvasItem,
    summary="保存会话画布（全量或增量，带 revision 校验）",
)
async def save_flow_canvas(
    session_key: str,
    body: FlowCanvasSaveRequest,
    request: Request,
    session: DbSession,
    user: FlowWriter,
    workspace_key: str = Query(...),
    workflow_key: str = Query(...),
) -> FlowCanvasItem:
    snapshot = await FlowStoreService(session).put_canvas(
        user.id,
        workspace_key,
        workflow_key,
        session_key,
        base_revision=body.base_revision,
        viewport=body.viewport,
        nodes=body.nodes,
        edges=body.edges,
        node_upserts=body.node_upserts,
        node_deletes=body.node_deletes,
        edge_upserts=body.edge_upserts,
        edge_deletes=body.edge_deletes,
        name=body.name,
        override=body.override,
        oss=_oss_client(request),
    )
    return _canvas_item(snapshot, workspace_key, workflow_key, session_key)


# ---------------------------------------------------------------------------
# 上传图
# ---------------------------------------------------------------------------


@store_router.post(
    "/sessions/{session_key}/images",
    response_model=FlowImageItem,
    summary="上传画布参考图（转存 OSS）",
)
async def upload_flow_image(
    session_key: str,
    request: Request,
    session: DbSession,
    user: FlowWriter,
    file: Annotated[UploadFile, File()],
    workspace_key: Annotated[str, Form()],
    workflow_key: Annotated[str, Form()],
    node_id: Annotated[str, Form()] = "",
) -> FlowImageItem:
    content = await file.read()
    row = await FlowStoreService(session).save_image(
        user.id,
        workspace_key,
        workflow_key,
        session_key,
        node_id,
        content,
        file.content_type or "image/png",
        _oss_client(request),
    )
    return FlowImageItem(
        node_id=node_id,
        sha256=row.sha256,
        url=row.url,
        mime=row.mime,
        bytes=row.bytes,
    )
