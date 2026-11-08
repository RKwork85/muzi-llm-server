"""按元数据发现技能。这里不读取 SKILL.md。"""

from __future__ import annotations

import re

from app.skills.discovery_models import (
    DecisionKind,
    DiscoveryMode,
    DiscoveryOutcome,
    DiscoveryPolicy,
    SkillCandidate,
)
from app.skills.errors import SkillError
from app.skills.models import SkillActor, SkillMetadata, SkillQuery, SkillStatus
from app.skills.registry import FileSkillRegistry

_CJK = re.compile(r"[\u4e00-\u9fff]{2,}")


class SkillDiscoveryService:
    def __init__(self, registry: FileSkillRegistry, policy: DiscoveryPolicy | None = None) -> None:
        self._registry = registry
        self._policy = policy or DiscoveryPolicy()

    @property
    def policy(self) -> DiscoveryPolicy:
        return self._policy

    def discover(
        self,
        *,
        actor: SkillActor,
        task_text: str,
        mode: DiscoveryMode,
        explicit_skill_ids: tuple[str, ...] = (),
        explicit_names: tuple[str, ...] = (),
    ) -> DiscoveryOutcome:
        policy = self._policy
        if mode == DiscoveryMode.DISABLED:
            return DiscoveryOutcome(policy_version=policy.version)
        visible = [
            item
            for item in self._registry.query(SkillQuery(actor=actor))
            if item.status != SkillStatus.UNINSTALLED
        ]
        if mode == DiscoveryMode.EXPLICIT:
            return self._explicit(visible, explicit_skill_ids, explicit_names, policy)
        ranked = self._rank(task_text, visible, policy)
        return self._decide(ranked, mode, policy)

    def _explicit(
        self,
        visible: list[SkillMetadata],
        skill_ids: tuple[str, ...],
        names: tuple[str, ...],
        policy: DiscoveryPolicy,
    ) -> DiscoveryOutcome:
        if not skill_ids and not names:
            return DiscoveryOutcome(
                policy_version=policy.version,
                error_code="SKILL_NOT_FOUND",
            )
        selected: list[SkillCandidate] = []
        for skill_id in skill_ids:
            match = next((item for item in visible if item.skill_id == skill_id), None)
            candidate = self._require_enabled(match)
            if isinstance(candidate, str):
                return DiscoveryOutcome(policy_version=policy.version, error_code=candidate)
            selected.append(candidate)
        for name in names:
            match = next((item for item in visible if item.name == name), None)
            candidate = self._require_enabled(match)
            if isinstance(candidate, str):
                return DiscoveryOutcome(policy_version=policy.version, error_code=candidate)
            if all(item.skill_id != candidate.skill_id for item in selected):
                selected.append(candidate)
        if len(selected) > policy.max_selected:
            raise SkillError("SKILL_DISCOVERY_FAILED", "指定的技能过多")
        return DiscoveryOutcome(
            candidates=tuple(selected),
            decision=DecisionKind.EXPLICIT,
            selected_ids=tuple(item.skill_id for item in selected),
            policy_version=policy.version,
        )

    def _require_enabled(self, meta: SkillMetadata | None) -> SkillCandidate | str:
        if meta is None:
            return "SKILL_NOT_FOUND"
        if meta.status != SkillStatus.ENABLED:
            return "SKILL_NOT_AVAILABLE"
        return SkillCandidate(
            skill_id=meta.skill_id,
            name=meta.name,
            description=meta.description,
            version=meta.version,
            scope=meta.scope,
            score=1.0,
            reason_codes=("explicit",),
            matched_fields=("skill_id",),
            availability="enabled",
        )

    def _rank(
        self,
        task_text: str,
        visible: list[SkillMetadata],
        policy: DiscoveryPolicy,
    ) -> list[SkillCandidate]:
        found: list[SkillCandidate] = []
        seen: set[str] = set()
        for meta in visible:
            if meta.status != SkillStatus.ENABLED or meta.skill_id in seen:
                continue
            value, reasons, fields = _score(task_text, meta)
            if value < policy.recommend_threshold or not reasons:
                continue
            seen.add(meta.skill_id)
            found.append(
                SkillCandidate(
                    skill_id=meta.skill_id,
                    name=meta.name,
                    description=meta.description,
                    version=meta.version,
                    scope=meta.scope,
                    score=value,
                    reason_codes=tuple(reasons),
                    matched_fields=tuple(fields),
                    availability="enabled",
                )
            )
        found.sort(key=lambda item: (-item.score, item.name, item.skill_id))
        return found[: policy.max_candidates]

    def _decide(
        self,
        ranked: list[SkillCandidate],
        mode: DiscoveryMode,
        policy: DiscoveryPolicy,
    ) -> DiscoveryOutcome:
        if not ranked:
            return DiscoveryOutcome(policy_version=policy.version, decision=DecisionKind.NONE)
        strong = [item for item in ranked if item.score >= policy.auto_select_threshold]
        ambiguous = (
            len(strong) >= 2
            and strong[0].score - strong[1].score < 0.05
            and "name_exact" not in strong[0].reason_codes
        )
        if mode == DiscoveryMode.RECOMMENDED or ambiguous or not strong:
            marked = tuple(
                item.model_copy(update={"requires_confirmation": True})
                if ambiguous
                else item
                for item in ranked
            )
            decision = DecisionKind.RECOMMEND if ranked[0].score >= policy.recommend_threshold else DecisionKind.NONE
            return DiscoveryOutcome(
                candidates=marked,
                decision=decision,
                policy_version=policy.version,
                needs_clarification=ambiguous,
            )
        chosen = strong if policy.allow_multiple else strong[:1]
        chosen = chosen[: policy.max_selected]
        return DiscoveryOutcome(
            candidates=tuple(ranked),
            decision=DecisionKind.SELECT,
            selected_ids=tuple(item.skill_id for item in chosen),
            policy_version=policy.version,
        )


def _score(task_text: str, meta: SkillMetadata) -> tuple[float, list[str], list[str]]:
    text = task_text.lower()
    score = 0.0
    reasons: list[str] = []
    fields: list[str] = []
    if meta.name.lower() in text:
        score = 0.96
        reasons.append("name_exact")
        fields.append("name")
    else:
        parts = [part for part in meta.name.split("-") if len(part) >= 4]
        if parts and all(part in text for part in parts):
            score = 0.84
            reasons.append("name_token")
            fields.append("name")
    if any(tag in text for tag in meta.tags):
        score = max(score, 0.8)
        reasons.append("tag")
        fields.append("tags")
    if meta.category and meta.category in text:
        score = max(score, 0.6)
        reasons.append("category")
        fields.append("category")
    hits = sum(1 for token in _cjk_tokens(meta.description) if token in task_text)
    if hits:
        score = max(score, min(0.7, 0.35 + 0.1 * hits))
        reasons.append("description")
        fields.append("description")
    return round(min(score, 1.0), 4), reasons, fields


def _cjk_tokens(text: str) -> list[str]:
    found: list[str] = []
    for token in _CJK.findall(text):
        if token not in found:
            found.append(token)
        if len(token) > 2:
            for index in range(len(token) - 1):
                piece = token[index : index + 2]
                if piece not in found:
                    found.append(piece)
    return found
