"""技能目录与技能包（bundle）管理。

- ``GET /skills`` 只返回已启用技能的元数据（发现层，不返回正文）。
- ``GET /skills/bundles`` 返回当前用户可用的技能包（一个包一张卡片）。
- ``POST /skills/bundles`` 上传一个 zip 技能包，整体安装。
- ``DELETE /skills/bundles/{bundle_id}`` 卸载整个技能包。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, UploadFile
from pydantic import BaseModel, Field

from app.api.v1.endpoints.mcp import optional_mcp
from app.core.exceptions import LoginRequiredError, SkillFlowError
from app.mcp.service import McpService
from app.skills.errors import SkillError
from app.skills.models import SkillActor, SkillBundle, SkillQuery, SkillScope, SkillStatus
from app.skills.registry import FileSkillRegistry

router = APIRouter(tags=["skills"])


class SkillCard(BaseModel):
    id: str
    name: str
    description: str
    source: str = ""


class SkillListResponse(BaseModel):
    skills: list[SkillCard] = Field(default_factory=list)


class BundleSkillCard(BaseModel):
    id: str
    name: str
    description: str


class BundleCard(BaseModel):
    bundle_id: str
    name: str
    skill_count: int
    skills: list[BundleSkillCard] = Field(default_factory=list)


class BundleListResponse(BaseModel):
    bundles: list[BundleCard] = Field(default_factory=list)


def _registry(request: Request) -> FileSkillRegistry:
    runtime = getattr(request.app.state, "skill_runtime", None)
    if runtime is None:
        raise SkillFlowError("SKILL_RUNTIME_UNAVAILABLE", "技能运行时未开启")
    return runtime.orchestrator.registry  # type: ignore[attr-defined]


def _bundle_card(bundle: SkillBundle) -> BundleCard:
    return BundleCard(
        bundle_id=bundle.bundle_id,
        name=bundle.name,
        skill_count=bundle.skill_count,
        skills=[
            BundleSkillCard(id=item.skill_id, name=item.name, description=item.description)
            for item in bundle.skills
        ],
    )


@router.get("/skills", response_model=SkillListResponse, summary="列出已启用的技能")
def list_skills(request: Request) -> SkillListResponse:
    runtime = getattr(request.app.state, "skill_runtime", None)
    if runtime is None:
        return SkillListResponse()
    registry = runtime.orchestrator.registry
    actor = SkillActor()
    cards: list[SkillCard] = []
    for meta in registry.query(SkillQuery(actor=actor, status=SkillStatus.ENABLED)):
        skill = registry.get(meta.skill_id, actor=actor)
        cards.append(
            SkillCard(
                id=meta.skill_id,
                name=meta.name,
                description=meta.description,
                source=skill.source.label,
            )
        )
    return SkillListResponse(skills=cards)


@router.get("/skills/bundles", response_model=BundleListResponse, summary="列出技能包")
async def list_bundles(
    request: Request,
    mcp: McpService | None = Depends(optional_mcp),
) -> BundleListResponse:
    registry = _registry(request)
    user_id = await mcp.user_id_from_request(request) if mcp is not None else None
    bundles = registry.list_bundles(actor=SkillActor(user_id=user_id))
    return BundleListResponse(bundles=[_bundle_card(item) for item in bundles])


@router.post("/skills/bundles", response_model=BundleCard, summary="上传并安装一个技能包")
async def upload_bundle(
    request: Request,
    file: UploadFile = File(...),
    mcp: McpService | None = Depends(optional_mcp),
) -> BundleCard:
    registry = _registry(request)
    user_id = await mcp.user_id_from_request(request) if mcp is not None else None
    if not user_id:
        raise LoginRequiredError()
    payload = await file.read()
    name = file.filename or "skill.zip"
    try:
        bundle = registry.import_bundle(
            payload,
            actor=SkillActor(user_id=user_id),
            name=name,
            scope=SkillScope.USER,
        )
    except SkillError as exc:
        raise SkillFlowError(exc.code, exc.message) from exc
    return _bundle_card(bundle)


@router.delete("/skills/bundles/{bundle_id}", response_model=BundleCard, summary="卸载技能包")
async def uninstall_bundle(
    bundle_id: str,
    request: Request,
    mcp: McpService | None = Depends(optional_mcp),
) -> BundleCard:
    registry = _registry(request)
    user_id = await mcp.user_id_from_request(request) if mcp is not None else None
    if not user_id:
        raise LoginRequiredError()
    try:
        bundle = registry.uninstall_bundle(bundle_id, actor=SkillActor(user_id=user_id))
    except SkillError as exc:
        raise SkillFlowError(exc.code, exc.message) from exc
    return _bundle_card(bundle)
