"""把同一套运行时结果接到 Chat 或 Agent 已有提示词后面。"""

from __future__ import annotations

from typing import Literal

from app.core.exceptions import SkillFlowError
from app.skills.discovery_models import DiscoveryMode
from app.skills.orchestrator import OrchestratorResult, SkillOrchestrator


class SkillPromptAdapter:
    def __init__(self, orchestrator: SkillOrchestrator) -> None:
        self.orchestrator = orchestrator

    def apply(
        self,
        base_prompt: str,
        *,
        principal_id: str,
        task_id: str,
        task_text: str,
        surface: Literal["chat", "agent"],
        mode: DiscoveryMode,
        conversation_id: str | None = None,
        explicit_skill_ids: tuple[str, ...] = (),
        explicit_names: tuple[str, ...] = (),
        resource_paths: tuple[str, ...] = (),
    ) -> tuple[str, OrchestratorResult]:
        result = self.orchestrator.prepare(
            principal_id=principal_id,
            task_id=task_id,
            task_text=task_text,
            surface=surface,
            mode=mode,
            system_text=base_prompt,
            conversation_id=conversation_id,
            explicit_skill_ids=explicit_skill_ids,
            explicit_names=explicit_names,
            resource_paths=resource_paths,
        )
        if result.error_code and not result.fallback:
            raise SkillFlowError(result.error_code)
        if not result.prompt_block:
            return base_prompt, result
        if not result.prompt_block.startswith("以下为用户安装的技能说明"):
            raise SkillFlowError("SKILL_RUNTIME_FAILED")
        return f"{base_prompt}\n\n{result.prompt_block}", result
