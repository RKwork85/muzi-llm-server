"""生成资产查询接口：资产页拉取当前用户的生成产物（ComfyUI 等生成链路）。"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import get_authenticated_user
from app.core.database.deps import DbSession
from app.models.assets import GeneratedAsset
from app.models.tables import User

router = APIRouter(prefix="/assets", tags=["assets"])


class GeneratedAssetItem(BaseModel):
    id: str
    kind: str
    workflow_id: str | None
    job_id: str | None
    prompt: str | None
    filename: str | None
    subfolder: str | None
    type: str | None
    url: str | None
    created_at: str


class GeneratedAssetListResponse(BaseModel):
    items: list[GeneratedAssetItem]
    total: int


@router.get("", response_model=GeneratedAssetListResponse, summary="列出当前用户的生成资产")
async def list_generated_assets(
    session: DbSession,
    user: Annotated[User, Depends(get_authenticated_user)],
    kind: Literal["image", "video"] | None = Query(default=None, description="按类型过滤"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> GeneratedAssetListResponse:
    conds = [GeneratedAsset.user_id == user.id]
    if kind:
        conds.append(GeneratedAsset.kind == kind)

    total = await session.scalar(
        select(func.count()).select_from(GeneratedAsset).where(*conds)
    )
    rows = (
        await session.execute(
            select(GeneratedAsset)
            .where(*conds)
            .order_by(GeneratedAsset.created_at.desc(), GeneratedAsset.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    items = [
        GeneratedAssetItem(
            id=str(a.id),
            kind=a.kind,
            workflow_id=a.workflow_id,
            job_id=a.job_id,
            prompt=a.prompt,
            filename=a.filename,
            subfolder=a.subfolder,
            type=a.type,
            url=a.url,
            created_at=a.created_at.isoformat(),
        )
        for a in rows
    ]
    return GeneratedAssetListResponse(items=items, total=int(total or 0))
