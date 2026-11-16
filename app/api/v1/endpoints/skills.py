"""技能目录与技能包（bundle）管理。

- ``GET /skills`` 只返回已启用技能的元数据（发现层，不返回正文）。
- ``GET /skills/bundles`` 返回当前用户可用的技能包（一个包一张卡片）。
- ``POST /skills/bundles`` 上传一个 zip 技能包，整体安装。
- ``DELETE /skills/bundles/{bundle_id}`` 卸载整个技能包。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

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
    scope: str = ""
    executable: bool = False


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


class SkillHealthResponse(BaseModel):
    skills_root_mounted: bool
    runtime_attached: bool
    sandbox_alive: bool
    sandbox_backend: str = "subprocess"


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
                scope=meta.scope.value,
                executable=_is_executable(skill),
            )
        )
    return SkillListResponse(skills=cards)


@router.get("/skills/installed", response_model=SkillListResponse, summary="合并系统与当前用户已安装技能")
async def list_installed(
    request: Request,
    mcp: McpService | None = Depends(optional_mcp),
) -> SkillListResponse:
    """合并 system 全量 + 当前 user 的 ENABLED 技能，同名时用户覆盖系统。

    返回体用 ``scope`` 区分 ``"system"`` / ``"user"``，前端据此在 ``/`` 菜单分区展示。
    """
    runtime = getattr(request.app.state, "skill_runtime", None)
    if runtime is None:
        return SkillListResponse()
    registry = runtime.orchestrator.registry
    user_id = await mcp.user_id_from_request(request) if mcp is not None else None
    actor = SkillActor(user_id=user_id)
    cards: list[SkillCard] = []
    for meta in registry.installed(actor=actor):
        skill = registry.get(meta.skill_id, actor=actor)
        cards.append(
            SkillCard(
                id=meta.skill_id,
                name=meta.name,
                description=meta.description,
                source=skill.source.label,
                scope=meta.scope.value,
                executable=_is_executable(skill),
            )
        )
    return SkillListResponse(skills=cards)


def _is_executable(skill) -> bool:
    runtime = getattr(skill, "runtime", None)
    return bool(runtime is not None and getattr(runtime, "entrypoint", None))


@router.get("/skills/health", response_model=SkillHealthResponse, summary="技能系统健康检查")
async def skills_health(request: Request) -> SkillHealthResponse:
    """报告 skills_root 挂载、运行时附着与沙盒执行器连通性。"""
    skill_runtime = getattr(request.app.state, "skill_runtime", None)
    attached = False
    if skill_runtime is not None:
        chat_service = getattr(request.app.state, "chat_service", None)
        agent_runtime = getattr(request.app.state, "agent_runtime", None)
        attached_chat = chat_service is not None and getattr(chat_service, "skill_runtime", None) is skill_runtime
        attached_agent = agent_runtime is not None and getattr(agent_runtime, "skill_runtime", None) is skill_runtime
        attached = attached_chat and attached_agent
    return SkillHealthResponse(
        skills_root_mounted=skill_runtime is not None,
        runtime_attached=attached,
        sandbox_alive=Path(sys.executable).is_file(),
    )


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
    audit = getattr(request.app.state, "skill_audit", None)
    if audit is not None:
        actor = SkillActor(user_id=user_id)
        for meta in bundle.skills:
            asyncio.create_task(audit.audit_and_settle(meta.skill_id, actor=actor))
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
