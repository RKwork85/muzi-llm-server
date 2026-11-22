"""flow canvas rows + image refs

把画布从「整份 JSONB」推进到「一个节点 / 一条连线一行」：

- ``flow_canvas_nodes`` / ``flow_canvas_edges``：只重写改动的行，``revision`` 记录
  这一行最后在第几版变过，读端可以按 ``since`` 拿增量，删除留墓碑；
- ``flow_canvases`` 增加 ``node_count`` / ``edge_count``，旧快照列迁移后清空；
- ``flow_images`` 增加 ``ref_count`` / ``last_seen_at`` / ``orphaned_at``，
  引用关系落在 ``flow_image_refs``，孤儿图交给后台任务回收。

upgrade 会把已有快照拆成行，并按节点里的 ``data.src`` 回填引用计数。

Revision ID: c7d2e8f4a916
Revises: b1f4d7c3a902
Create Date: 2026-11-22 03:20:00.000000

"""

from __future__ import annotations

import uuid
from typing import Any, Iterable, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c7d2e8f4a916"
down_revision: Union[str, Sequence[str], None] = "b1f4d7c3a902"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = postgresql.JSONB(astext_type=sa.Text())

# 单条 INSERT 的批大小：节点 payload 可能不小，别把参数一次堆爆。
_BATCH = 200


def _ident(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    return str(item.get("id") or "").strip()[:64]


def _image_urls(payload: Any) -> set[str]:
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


def _chunks(items: list[dict[str, Any]]) -> Iterable[list[dict[str, Any]]]:
    for index in range(0, len(items), _BATCH):
        yield items[index : index + _BATCH]


def upgrade() -> None:
    op.create_table(
        "flow_canvas_nodes",
        sa.Column("canvas_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("payload", _JSON, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("sort_index", sa.Integer(), server_default="0", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["canvas_id"],
            ["flow_canvases.id"],
            name="fk_flow_canvas_nodes_canvas_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canvas_id", "node_id", name="uq_flow_canvas_nodes_item"),
        comment="画布节点：一条一行，只重写改动的行；deleted_at 即墓碑。",
    )
    op.create_index(
        "ix_flow_canvas_nodes_canvas_order",
        "flow_canvas_nodes",
        ["canvas_id", "sort_index"],
        unique=False,
    )
    op.create_index(
        "ix_flow_canvas_nodes_canvas_rev",
        "flow_canvas_nodes",
        ["canvas_id", "revision"],
        unique=False,
    )

    op.create_table(
        "flow_canvas_edges",
        sa.Column("canvas_id", sa.Uuid(), nullable=False),
        sa.Column("edge_id", sa.String(length=64), nullable=False),
        sa.Column("payload", _JSON, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("sort_index", sa.Integer(), server_default="0", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["canvas_id"],
            ["flow_canvases.id"],
            name="fk_flow_canvas_edges_canvas_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canvas_id", "edge_id", name="uq_flow_canvas_edges_item"),
        comment="画布连线：一条一行，只重写改动的行；deleted_at 即墓碑。",
    )
    op.create_index(
        "ix_flow_canvas_edges_canvas_order",
        "flow_canvas_edges",
        ["canvas_id", "sort_index"],
        unique=False,
    )
    op.create_index(
        "ix_flow_canvas_edges_canvas_rev",
        "flow_canvas_edges",
        ["canvas_id", "revision"],
        unique=False,
    )

    op.create_table(
        "flow_image_refs",
        sa.Column("image_id", sa.Uuid(), nullable=False),
        sa.Column("canvas_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.String(length=64), server_default="", nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["image_id"],
            ["flow_images.id"],
            name="fk_flow_image_refs_image_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["canvas_id"],
            ["flow_canvases.id"],
            name="fk_flow_image_refs_canvas_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("image_id", "canvas_id", "node_id", name="uq_flow_image_refs_item"),
        comment="画布节点 → 上传图的引用；ref_count 由它汇总。",
    )
    op.create_index(
        "ix_flow_image_refs_canvas", "flow_image_refs", ["canvas_id"], unique=False
    )
    op.create_index("ix_flow_image_refs_image", "flow_image_refs", ["image_id"], unique=False)

    op.add_column(
        "flow_canvases",
        sa.Column("node_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "flow_canvases",
        sa.Column("edge_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "flow_images", sa.Column("ref_count", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "flow_images", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "flow_images", sa.Column("orphaned_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_flow_images_orphan",
        "flow_images",
        ["user_id", "ref_count", "created_at"],
        unique=False,
    )
    op.alter_column(
        "flow_canvases",
        "nodes",
        existing_type=_JSON,
        comment="旧版整份快照，迁移后清空；新数据写 flow_canvas_nodes。",
    )
    op.alter_column(
        "flow_canvases",
        "edges",
        existing_type=_JSON,
        comment="旧版整份快照，迁移后清空；新数据写 flow_canvas_edges。",
    )

    _explode_snapshots(op.get_bind())


def _explode_snapshots(bind: sa.Connection) -> None:
    canvases = list(
        bind.execute(
            sa.text(
                "select id, user_id, revision, nodes, edges, updated_at from flow_canvases"
            )
        ).mappings()
    )
    if not canvases:
        return

    images = {
        (str(row["user_id"]), row["url"]): row["id"]
        for row in bind.execute(sa.text("select id, user_id, url from flow_images")).mappings()
    }

    node_values: list[dict[str, Any]] = []
    edge_values: list[dict[str, Any]] = []
    ref_values: list[dict[str, Any]] = []
    ref_seen: set[tuple[str, str, str]] = set()

    for canvas in canvases:
        canvas_id = canvas["id"]
        user_id = str(canvas["user_id"])
        revision = int(canvas["revision"] or 1)
        stamp = canvas["updated_at"]

        used_nodes: set[str] = set()
        for index, item in enumerate(canvas["nodes"] or []):
            ident = _ident(item)
            if not ident or ident in used_nodes:
                continue
            used_nodes.add(ident)
            node_values.append(
                {
                    "id": uuid.uuid4(),
                    "canvas_id": canvas_id,
                    "node_id": ident,
                    "payload": item,
                    "sort_index": index,
                    "revision": revision,
                    "created_at": stamp,
                    "updated_at": stamp,
                }
            )
            for url in _image_urls(item):
                image_id = images.get((user_id, url))
                if image_id is None:
                    continue
                marker = (str(image_id), str(canvas_id), ident)
                if marker in ref_seen:
                    continue
                ref_seen.add(marker)
                ref_values.append(
                    {
                        "id": uuid.uuid4(),
                        "image_id": image_id,
                        "canvas_id": canvas_id,
                        "node_id": ident,
                        "created_at": stamp,
                    }
                )

        used_edges: set[str] = set()
        for index, item in enumerate(canvas["edges"] or []):
            ident = _ident(item)
            if not ident or ident in used_edges:
                continue
            used_edges.add(ident)
            edge_values.append(
                {
                    "id": uuid.uuid4(),
                    "canvas_id": canvas_id,
                    "edge_id": ident,
                    "payload": item,
                    "sort_index": index,
                    "revision": revision,
                    "created_at": stamp,
                    "updated_at": stamp,
                }
            )

    meta = sa.MetaData()
    nodes_t = sa.Table("flow_canvas_nodes", meta, autoload_with=bind)
    edges_t = sa.Table("flow_canvas_edges", meta, autoload_with=bind)
    refs_t = sa.Table("flow_image_refs", meta, autoload_with=bind)

    for chunk in _chunks(node_values):
        bind.execute(sa.insert(nodes_t), chunk)
    for chunk in _chunks(edge_values):
        bind.execute(sa.insert(edges_t), chunk)
    for chunk in _chunks(ref_values):
        bind.execute(sa.insert(refs_t), chunk)

    # 快照已经进行表：清空旧列，别让同一份画布存两份。
    bind.execute(
        sa.text(
            "update flow_canvases c set "
            "  nodes = '[]'::jsonb, edges = '[]'::jsonb, "
            "  node_count = (select count(*) from flow_canvas_nodes n "
            "                where n.canvas_id = c.id and n.deleted_at is null), "
            "  edge_count = (select count(*) from flow_canvas_edges e "
            "                where e.canvas_id = c.id and e.deleted_at is null)"
        )
    )
    # 引用计数按引用行对账一次，包含「已有图但没人引用」的 0。
    bind.execute(
        sa.text(
            "update flow_images i set "
            "  ref_count = (select count(*) from flow_image_refs r where r.image_id = i.id)"
        )
    )
    bind.execute(
        sa.text(
            "update flow_images set orphaned_at = now() "
            "where ref_count = 0 and orphaned_at is null"
        )
    )


def downgrade() -> None:
    # 先把行拼回整份快照，再回退表结构，避免降级丢画布。
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "update flow_canvases c set "
            "  nodes = coalesce(("
            "    select jsonb_agg(n.payload order by n.sort_index, n.created_at) "
            "    from flow_canvas_nodes n where n.canvas_id = c.id and n.deleted_at is null"
            "  ), '[]'::jsonb), "
            "  edges = coalesce(("
            "    select jsonb_agg(e.payload order by e.sort_index, e.created_at) "
            "    from flow_canvas_edges e where e.canvas_id = c.id and e.deleted_at is null"
            "  ), '[]'::jsonb)"
        )
    )

    op.drop_index("ix_flow_images_orphan", table_name="flow_images")
    op.drop_column("flow_images", "orphaned_at")
    op.drop_column("flow_images", "last_seen_at")
    op.drop_column("flow_images", "ref_count")
    op.alter_column("flow_canvases", "edges", existing_type=_JSON, comment=None)
    op.alter_column("flow_canvases", "nodes", existing_type=_JSON, comment=None)
    op.drop_column("flow_canvases", "edge_count")
    op.drop_column("flow_canvases", "node_count")

    op.drop_index("ix_flow_image_refs_image", table_name="flow_image_refs")
    op.drop_index("ix_flow_image_refs_canvas", table_name="flow_image_refs")
    op.drop_table("flow_image_refs")
    op.drop_index("ix_flow_canvas_edges_canvas_rev", table_name="flow_canvas_edges")
    op.drop_index("ix_flow_canvas_edges_canvas_order", table_name="flow_canvas_edges")
    op.drop_table("flow_canvas_edges")
    op.drop_index("ix_flow_canvas_nodes_canvas_rev", table_name="flow_canvas_nodes")
    op.drop_index("ix_flow_canvas_nodes_canvas_order", table_name="flow_canvas_nodes")
    op.drop_table("flow_canvas_nodes")
