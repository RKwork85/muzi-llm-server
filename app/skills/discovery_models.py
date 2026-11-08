"""发现阶段的附加契约。身份由调用方传入，不从请求正文里取所有者。"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.skills.models import SkillScope


class DiscoveryMode(str, Enum):
    AUTO = "auto"
    RECOMMENDED = "recommended"
    EXPLICIT = "explicit"
    DISABLED = "disabled"


class DecisionKind(str, Enum):
    NONE = "none"
    RECOMMEND = "recommend"
    SELECT = "select"
    EXPLICIT = "explicit"


class DiscoveryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = "m3.1"
    auto_select_threshold: float = Field(default=0.72, ge=0, le=1)
    recommend_threshold: float = Field(default=0.4, ge=0, le=1)
    max_candidates: int = Field(default=5, ge=1, le=8)
    max_selected: int = Field(default=3, ge=1, le=3)
    allow_multiple: bool = True


class SkillCandidate(BaseModel):
    """候选只含元数据。分数不是授权。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: str
    name: str
    description: str
    version: str
    scope: SkillScope
    score: float = Field(ge=0, le=1)
    reason_codes: tuple[str, ...]
    matched_fields: tuple[str, ...]
    availability: Literal["enabled"]
    requires_confirmation: bool = False


class DiscoveryOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidates: tuple[SkillCandidate, ...] = ()
    decision: DecisionKind = DecisionKind.NONE
    selected_ids: tuple[str, ...] = ()
    policy_version: str
    needs_clarification: bool = False
    error_code: str | None = None


class LoadedSkill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: str
    name: str
    version: str
    content_digest: str
    instruction_body: str
    owner_id: str | None
    loaded_at: str
    untrusted: Literal[True] = True
