"""已启用技能目录。只返回发现所需的元数据，不返回技能正文。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.skills.models import SkillActor, SkillQuery, SkillStatus

router = APIRouter(tags=["skills"])


class SkillCard(BaseModel):
    id: str
    name: str
    description: str
    source: str = ""


class SkillListResponse(BaseModel):
    skills: list[SkillCard] = Field(default_factory=list)


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
