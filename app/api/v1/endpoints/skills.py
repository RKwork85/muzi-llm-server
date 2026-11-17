"""技能目录与技能包（bundle）管理。

- ``GET /skills`` 只返回已启用技能的元数据（发现层，不返回正文）。
- ``GET /skills/bundles`` 返回当前用户可用的技能包（一个包一张卡片）。
- ``POST /skills/bundles`` 上传一个 zip 技能包，整体安装。
- ``DELETE /skills/bundles/{bundle_id}`` 卸载整个技能包。
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_authenticated_user
from app.api.v1.endpoints.mcp import optional_mcp
from app.core.database.deps import DbSession, DbSessionOptional
from app.core.exceptions import LoginRequiredError, SkillFlowError
from app.mcp.service import McpService
from app.models.tables import SkillCustomization, User
from app.schemas.skill_customization import (
    SkillCustomizationOut,
    SkillDisplayNameUpdate,
    SkillIconUpload,
)
from app.services.skill_customization_service import SkillCustomizationService
from app.skills.errors import SkillError
from app.skills.models import SkillActor, SkillBundle, SkillQuery, SkillScope, SkillStatus
from app.skills.registry import FileSkillRegistry

router = APIRouter(tags=["skills"])
AuthenticatedUser = Annotated[User, Depends(get_authenticated_user)]


class SkillCard(BaseModel):
    id: str
    name: str
    description: str
    source: str = ""
    scope: str = ""
    executable: bool = False
    icon: str = ""
    display_name: str = ""


class SkillListResponse(BaseModel):
    skills: list[SkillCard] = Field(default_factory=list)


class BundleSkillCard(BaseModel):
    id: str
    name: str
    description: str
    icon: str = ""
    display_name: str = ""


class BundleCard(BaseModel):
    bundle_id: str
    name: str
    skill_count: int
    icon: str = ""
    display_name: str = ""
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


class _Customization(BaseModel):
    display_name: str = ""
    icon: str = ""


def _bundle_card(bundle: SkillBundle, customs: dict[str, _Customization] | None = None) -> BundleCard:
    customs = customs or {}
    skills = [
        BundleSkillCard(
            id=item.skill_id,
            name=item.name,
            description=item.description,
            icon=customs.get(item.skill_id, _Customization()).icon,
            display_name=customs.get(item.skill_id, _Customization()).display_name,
        )
        for item in bundle.skills
    ]
    first = skills[0] if skills else None
    return BundleCard(
        bundle_id=bundle.bundle_id,
        name=bundle.name,
        skill_count=bundle.skill_count,
        icon=first.icon if first else "",
        display_name=first.display_name if first else "",
        skills=skills,
    )


async def _load_customizations(
    session: AsyncSession | None, user_id: str | None
) -> dict[str, _Customization]:
    """按 skill_id 读取当前用户的自定义显示名与图标。user_id 非法、为空或数据库不可用时返回空映射。"""
    if not user_id or session is None:
        return {}
    try:
        owner = uuid.UUID(user_id)
    except ValueError:
        return {}
    rows = await session.scalars(
        select(SkillCustomization).where(SkillCustomization.user_id == owner)
    )
    return {
        row.skill_id: _Customization(display_name=row.display_name or "", icon=row.icon or "")
        for row in rows
    }


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
    session: DbSessionOptional,
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
    customs = await _load_customizations(session, user_id)
    cards: list[SkillCard] = []
    for meta in registry.installed(actor=actor):
        skill = registry.get(meta.skill_id, actor=actor)
        custom = customs.get(meta.skill_id, _Customization())
        cards.append(
            SkillCard(
                id=meta.skill_id,
                name=meta.name,
                description=meta.description,
                source=skill.source.label,
                scope=meta.scope.value,
                executable=_is_executable(skill),
                icon=custom.icon,
                display_name=custom.display_name,
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
    session: DbSessionOptional,
    mcp: McpService | None = Depends(optional_mcp),
) -> BundleListResponse:
    registry = _registry(request)
    user_id = await mcp.user_id_from_request(request) if mcp is not None else None
    bundles = registry.list_bundles(actor=SkillActor(user_id=user_id))
    customs = await _load_customizations(session, user_id)
    return BundleListResponse(bundles=[_bundle_card(item, customs) for item in bundles])


@router.post("/skills/bundles", response_model=BundleCard, summary="上传并安装一个技能包")
async def upload_bundle(
    request: Request,
    session: DbSessionOptional,
    file: UploadFile = File(...),
    display_name: str | None = Form(None),
    icon: str | None = Form(None),
    mcp: McpService | None = Depends(optional_mcp),
) -> BundleCard:
    registry = _registry(request)
    user_id = await mcp.user_id_from_request(request) if mcp is not None else None
    if not user_id:
        raise LoginRequiredError()
    name = file.filename or "skill.zip"
    # 流式导入：直接把底层 spooled 文件交给 ZipFile，避免 await file.read()
    # 把整包再复制一份进内存；解压/校验/落盘放到线程池，不阻塞事件循环。
    await file.seek(0)
    try:
        bundle = await asyncio.to_thread(
            registry.import_bundle,
            file.file,
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

    # 上传时可选的自定义显示名与图标：对包内每个技能统一应用，数据库不可用时静默跳过。
    custom = _Customization()
    if session is not None and bundle.skills and (display_name or icon):
        service = SkillCustomizationService(session)
        try:
            owner = uuid.UUID(user_id)
        except ValueError:
            owner = None
        if owner is not None:
            cleaned_name = service.validate_display_name(display_name) if display_name else None
            if icon:
                service.validate_data_url(icon)
            for meta in bundle.skills:
                if cleaned_name:
                    await service.set_display_name(owner, meta.skill_id, cleaned_name)
                if icon:
                    await service.set_icon(owner, meta.skill_id, icon)
            custom = _Customization(display_name=cleaned_name or "", icon=icon or "")
            await session.flush()
    result = _bundle_card(bundle)
    if custom.display_name:
        result.display_name = custom.display_name
    if custom.icon:
        result.icon = custom.icon
        for skill in result.skills:
            skill.icon = custom.icon
    return result


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


@router.put("/skills/{skill_id}/icon", response_model=SkillCustomizationOut, summary="修改技能图标")
async def update_skill_icon(
    skill_id: str,
    body: SkillIconUpload,
    request: Request,
    session: DbSession,
    user: AuthenticatedUser,
) -> SkillCustomizationOut:
    """为当前用户上传的技能设置自定义图标。图标以 data URL 存库。"""
    registry = _registry(request)
    actor = SkillActor(user_id=str(user.id))
    # 校验技能存在且归属当前用户，非本人技能抛 SKILL_NOT_FOUND。
    registry.get(skill_id, actor=actor)
    service = SkillCustomizationService(session)
    await service.set_icon(user.id, skill_id, body.data_url)
    await session.flush()
    return SkillCustomizationOut(skill_id=skill_id, icon=body.data_url)


@router.delete("/skills/{skill_id}/icon", response_model=SkillCustomizationOut, summary="重置技能图标")
async def reset_skill_icon(
    skill_id: str,
    request: Request,
    session: DbSession,
    user: AuthenticatedUser,
) -> SkillCustomizationOut:
    """删除自定义图标，回落为默认标识。"""
    registry = _registry(request)
    actor = SkillActor(user_id=str(user.id))
    registry.get(skill_id, actor=actor)
    service = SkillCustomizationService(session)
    await service.remove_icon(user.id, skill_id)
    return SkillCustomizationOut(skill_id=skill_id, icon=None)


@router.put("/skills/{skill_id}/display-name", response_model=SkillCustomizationOut, summary="重命名技能显示名")
async def update_skill_display_name(
    skill_id: str,
    body: SkillDisplayNameUpdate,
    request: Request,
    session: DbSession,
    user: AuthenticatedUser,
) -> SkillCustomizationOut:
    """为当前用户上传的技能设置自定义显示名。"""
    registry = _registry(request)
    actor = SkillActor(user_id=str(user.id))
    registry.get(skill_id, actor=actor)
    service = SkillCustomizationService(session)
    cleaned = await service.set_display_name(user.id, skill_id, body.display_name)
    await session.flush()
    return SkillCustomizationOut(skill_id=skill_id, display_name=cleaned)


@router.delete("/skills/{skill_id}/display-name", response_model=SkillCustomizationOut, summary="重置技能显示名")
async def reset_skill_display_name(
    skill_id: str,
    request: Request,
    session: DbSession,
    user: AuthenticatedUser,
) -> SkillCustomizationOut:
    """删除自定义显示名，回落为原始名称。"""
    registry = _registry(request)
    actor = SkillActor(user_id=str(user.id))
    registry.get(skill_id, actor=actor)
    service = SkillCustomizationService(session)
    await service.remove_display_name(user.id, skill_id)
    return SkillCustomizationOut(skill_id=skill_id, display_name=None)
