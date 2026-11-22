"""flow storage normalized

把 AI 画布从「一份 JSON 文档」拆成应用、会话、画布、图片四张表：
- flow_apps：我的空间应用，一条一行，避免整份列表覆盖写；
- flow_sessions：会话元信息；
- flow_canvases：画布快照 + revision；
- flow_images：上传图的 OSS 地址与内容哈希。

upgrade 时把 flow_documents 里的旧数据搬进新表，旧表保留只读。
canvas / sessions 里有、apps 列表里没有的 ``my_*`` 应用会按会话名补建，
修掉旧版整份列表覆盖导致的「会话还在、我的空间里看不到」。

Revision ID: b1f4d7c3a902
Revises: f6a1c8e4b2d9
Create Date: 2026-11-22 02:40:00.000000

"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b1f4d7c3a902"
down_revision: Union[str, Sequence[str], None] = "f6a1c8e4b2d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = postgresql.JSONB(astext_type=sa.Text())


def _utc(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return fallback
    return fallback


def upgrade() -> None:
    op.create_table(
        "flow_apps",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_key", sa.String(length=64), nullable=False),
        sa.Column("app_key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("icon", sa.String(length=16), server_default="✧", nullable=False),
        sa.Column("accent", sa.String(length=32), server_default="#6366f1", nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("tags", _JSON, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("demo", sa.String(length=64), server_default="blank", nullable=False),
        sa.Column("origin", sa.String(length=32), server_default="user", nullable=False),
        sa.Column("source_key", sa.String(length=64), server_default="", nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_flow_apps_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "workspace_key", "app_key", name="uq_flow_apps_user_app"),
        comment="我的空间工作流应用：一条一行，画布数据在 flow_canvases。",
    )
    op.create_index(
        "ix_flow_apps_user_workspace",
        "flow_apps",
        ["user_id", "workspace_key", "updated_at"],
        unique=False,
    )

    op.create_table(
        "flow_sessions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_key", sa.String(length=64), nullable=False),
        sa.Column("workflow_key", sa.String(length=64), nullable=False),
        sa.Column("session_key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), server_default="", nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_flow_sessions_user_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "workspace_key",
            "workflow_key",
            "session_key",
            name="uq_flow_sessions_user_scope",
        ),
        comment="工作流会话元信息：名称与更新时间，画布在 flow_canvases。",
    )
    op.create_index(
        "ix_flow_sessions_user_workflow",
        "flow_sessions",
        ["user_id", "workspace_key", "workflow_key", "updated_at"],
        unique=False,
    )

    op.create_table(
        "flow_canvases",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("viewport", _JSON, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("nodes", _JSON, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("edges", _JSON, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("bytes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["flow_sessions.id"],
            name="fk_flow_canvases_session_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_flow_canvases_user_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", name="uq_flow_canvases_session"),
        comment="会话画布：viewport / nodes / edges 整份 JSONB，revision 单调递增。",
    )
    op.create_index(
        "ix_flow_canvases_user_updated",
        "flow_canvases",
        ["user_id", "updated_at"],
        unique=False,
    )

    op.create_table(
        "flow_images",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("node_id", sa.String(length=64), server_default="", nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("mime", sa.String(length=64), server_default="image/png", nullable=False),
        sa.Column("bytes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["flow_sessions.id"],
            name="fk_flow_images_session_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_flow_images_user_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "sha256", name="uq_flow_images_user_sha"),
        comment="画布上传图：OSS 地址 + sha256，字节在 muzi-oss。",
    )
    op.create_index("ix_flow_images_session", "flow_images", ["session_id"], unique=False)

    _migrate_documents(op.get_bind())


def _migrate_documents(bind: sa.Connection) -> None:
    rows = bind.execute(
        sa.text("select user_id, doc_key, payload, updated_at from flow_documents")
    ).mappings()
    docs = list(rows)
    if not docs:
        return

    meta = sa.MetaData()
    apps_t = sa.Table("flow_apps", meta, autoload_with=bind)
    sessions_t = sa.Table("flow_sessions", meta, autoload_with=bind)
    canvases_t = sa.Table("flow_canvases", meta, autoload_with=bind)

    apps: dict[tuple[str, str, str], dict[str, Any]] = {}
    sessions: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    canvases: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    for doc in docs:
        user_id = str(doc["user_id"])
        payload = doc["payload"] or {}
        updated_at = doc["updated_at"]
        parts = str(doc["doc_key"]).split(":")
        if len(parts) < 2:
            continue
        kind, workspace_key = parts[0], parts[1]

        if kind == "apps" and len(parts) == 2:
            for item in payload.get("apps") or []:
                app_key = str(item.get("key") or "").strip()
                if not app_key:
                    continue
                apps[(user_id, workspace_key, app_key)] = {
                    "user_id": uuid.UUID(user_id),
                    "workspace_key": workspace_key,
                    "app_key": app_key,
                    "name": str(item.get("name") or "未命名工作流应用")[:200],
                    "icon": str(item.get("icon") or "✧")[:16],
                    "accent": str(item.get("accent") or "#6366f1")[:32],
                    "description": str(item.get("desc") or "")[:2000],
                    "tags": [str(tag) for tag in (item.get("tags") or [])][:8],
                    "demo": str(item.get("demo") or "blank")[:64],
                    "origin": str(item.get("origin") or "user")[:32],
                    "source_key": str(item.get("sourceKey") or "")[:64],
                    "created_at": _utc(item.get("createdAt"), updated_at),
                    "updated_at": _utc(item.get("updatedAt"), updated_at),
                }
        elif kind == "sessions" and len(parts) == 3:
            workflow_key = parts[2]
            for item in payload.get("sessions") or []:
                session_key = str(item.get("id") or "").strip()
                if not session_key:
                    continue
                key = (user_id, workspace_key, workflow_key, session_key)
                sessions[key] = {
                    "name": str(item.get("name") or "")[:200],
                    "created_at": _utc(item.get("createdAt"), updated_at),
                    "updated_at": _utc(item.get("updatedAt"), updated_at),
                }
        elif kind == "canvas" and len(parts) == 4:
            workflow_key, session_key = parts[2], parts[3]
            key = (user_id, workspace_key, workflow_key, session_key)
            if not str(payload.get("version") or "").strip() and not payload.get("nodes"):
                continue
            viewport = payload.get("viewport") if isinstance(payload.get("viewport"), dict) else {}
            nodes = payload.get("nodes") if isinstance(payload.get("nodes"), list) else []
            edges = payload.get("edges") if isinstance(payload.get("edges"), list) else []
            encoded = json.dumps(
                {"viewport": viewport, "nodes": nodes, "edges": edges}, ensure_ascii=False
            ).encode("utf-8")
            canvases[key] = {
                "viewport": viewport,
                "nodes": nodes,
                "edges": edges,
                "bytes": len(encoded),
                "updated_at": updated_at,
                "created_at": updated_at,
            }
            sessions.setdefault(
                key,
                {"name": "", "created_at": updated_at, "updated_at": updated_at},
            )

    # 旧版整份列表被覆盖后，my_* 应用可能只剩会话与画布：按第一个会话名补建。
    for key in sessions:
        user_id, workspace_key, workflow_key, _ = key
        if not workflow_key.startswith("my_"):
            continue
        if (user_id, workspace_key, workflow_key) in apps:
            continue
        entry = sessions[key]
        apps[(user_id, workspace_key, workflow_key)] = {
            "user_id": uuid.UUID(user_id),
            "workspace_key": workspace_key,
            "app_key": workflow_key,
            "name": str(entry.get("name") or "我的工作流应用")[:200],
            "icon": "✧",
            "accent": "#6366f1",
            "description": "从旧版会话数据恢复",
            "tags": ["我的空间"],
            "demo": "blank",
            "origin": "user",
            "source_key": "",
            "created_at": entry.get("created_at") or now(),
            "updated_at": entry.get("updated_at") or now(),
        }

    if apps:
        stmt = postgresql.insert(apps_t).values(list(apps.values()))
        bind.execute(stmt.on_conflict_do_nothing())

    if sessions:
        bind.execute(
            postgresql.insert(sessions_t)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "user_id": uuid.UUID(key[0]),
                        "workspace_key": key[1],
                        "workflow_key": key[2],
                        "session_key": key[3],
                        "name": value.get("name") or "",
                        "created_at": value.get("created_at") or now(),
                        "updated_at": value.get("updated_at") or now(),
                    }
                    for key, value in sessions.items()
                ]
            )
            .on_conflict_do_nothing()
        )

    if canvases:
        ids = {
            (
                str(row["user_id"]),
                row["workspace_key"],
                row["workflow_key"],
                row["session_key"],
            ): row["id"]
            for row in bind.execute(
                sa.text(
                    "select user_id, workspace_key, workflow_key, session_key, id from flow_sessions"
                )
            ).mappings()
        }
        payload = []
        for key, value in canvases.items():
            session_id = ids.get(key)
            if session_id is None:
                continue
            payload.append(
                {
                    "id": uuid.uuid4(),
                    "user_id": uuid.UUID(key[0]),
                    "session_id": session_id,
                    "revision": 1,
                    "viewport": value["viewport"],
                    "nodes": value["nodes"],
                    "edges": value["edges"],
                    "bytes": value["bytes"],
                    "created_at": value.get("created_at") or now(),
                    "updated_at": value.get("updated_at") or now(),
                }
            )
        if payload:
            bind.execute(postgresql.insert(canvases_t).values(payload).on_conflict_do_nothing())


def now() -> datetime:
    return datetime.now(timezone.utc)


def downgrade() -> None:
    op.drop_index("ix_flow_images_session", table_name="flow_images")
    op.drop_table("flow_images")
    op.drop_index("ix_flow_canvases_user_updated", table_name="flow_canvases")
    op.drop_table("flow_canvases")
    op.drop_index("ix_flow_sessions_user_workflow", table_name="flow_sessions")
    op.drop_table("flow_sessions")
    op.drop_index("ix_flow_apps_user_workspace", table_name="flow_apps")
    op.drop_table("flow_apps")
