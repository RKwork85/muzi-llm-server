"""Chat 和 Agent 共用的发现、加载和激活入口。"""

from __future__ import annotations

import logging
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.skills.activation_store import ActivationStore, SkillSnapshot
from app.skills.context_builder import BuiltContext, ContextBuilder
from app.skills.discovery import SkillDiscoveryService
from app.skills.discovery_models import DecisionKind, DiscoveryMode, DiscoveryOutcome
from app.skills.errors import SkillError
from app.skills.loader import SkillPackageLoader
from app.skills.models import SkillActor
from app.skills.registry import FileSkillRegistry
from app.skills.resources import read_resource
from app.skills.runtime_state import RuntimePhase

logger = logging.getLogger("muzi_llm")


class OrchestratorResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surface: Literal["chat", "agent"]
    task_id: str
    principal_id: str
    decision: DecisionKind
    fallback: bool
    error_code: str | None = None
    prompt_block: str = ""
    activation_ids: tuple[str, ...] = ()
    skill_ids: tuple[str, ...] = ()
    versions: tuple[str, ...] = ()
    digests: tuple[str, ...] = ()
    duration_ms: int = 0
    discovery: DiscoveryOutcome | None = None


class SkillOrchestrator:
    def __init__(
        self,
        registry: FileSkillRegistry,
        store: ActivationStore,
        *,
        discovery: SkillDiscoveryService | None = None,
        loader: SkillPackageLoader | None = None,
        builder: ContextBuilder | None = None,
    ) -> None:
        self.registry = registry
        self.store = store
        self.discovery = discovery or SkillDiscoveryService(registry)
        self.loader = loader or SkillPackageLoader(registry)
        self.builder = builder or ContextBuilder()

    def prepare(
        self,
        *,
        principal_id: str,
        task_id: str,
        task_text: str,
        surface: Literal["chat", "agent"],
        mode: DiscoveryMode,
        system_text: str,
        conversation_id: str | None = None,
        conversation: tuple[str, ...] = (),
        explicit_skill_ids: tuple[str, ...] = (),
        explicit_names: tuple[str, ...] = (),
        resource_paths: tuple[str, ...] = (),
    ) -> OrchestratorResult:
        started = time.perf_counter()
        actor = SkillActor(user_id=principal_id or None)
        try:
            outcome = self.discovery.discover(
                actor=actor,
                task_text=task_text,
                mode=mode,
                explicit_skill_ids=explicit_skill_ids,
                explicit_names=explicit_names,
            )
        except SkillError as exc:
            return self._finish(
                surface=surface,
                task_id=task_id,
                principal_id=principal_id,
                decision=DecisionKind.NONE,
                fallback=False,
                error_code=exc.code,
                started=started,
            )
        if outcome.error_code:
            return self._finish(
                surface=surface,
                task_id=task_id,
                principal_id=principal_id,
                decision=outcome.decision,
                fallback=False,
                error_code=outcome.error_code,
                discovery=outcome,
                started=started,
            )
        if outcome.decision not in {DecisionKind.SELECT, DecisionKind.EXPLICIT}:
            return self._finish(
                surface=surface,
                task_id=task_id,
                principal_id=principal_id,
                decision=outcome.decision,
                fallback=True,
                discovery=outcome,
                started=started,
            )
        if not principal_id:
            return self._finish(
                surface=surface,
                task_id=task_id,
                principal_id=principal_id,
                decision=outcome.decision,
                fallback=False,
                error_code="SKILL_NOT_ACCESSIBLE",
                discovery=outcome,
                started=started,
            )
        opened: list[SkillSnapshot] = []
        try:
            for skill_id in outcome.selected_ids:
                candidate = next(item for item in outcome.candidates if item.skill_id == skill_id)
                loaded = self.loader.load(skill_id, candidate.version, actor=actor)
                snapshot, created = self.store.open(
                    loaded,
                    principal_id=principal_id,
                    task_id=task_id,
                    conversation_id=conversation_id,
                )
                if created:
                    self.registry.reserve(snapshot.skill_id, actor=actor)
                opened.append(snapshot)
            resources = self._resources(opened, resource_paths, actor)
            built = self.builder.build(
                system_text=system_text,
                conversation=conversation,
                user_task=task_text,
                snapshots=tuple(opened),
                resources=resources,
            )
        except SkillError as exc:
            for snapshot in opened:
                try:
                    self.store.fail(snapshot.activation_id, principal_id=principal_id, error_code=exc.code)
                    self.registry.release(snapshot.skill_id, actor=actor)
                except SkillError:
                    logger.info("skill activation fail skipped code=%s", exc.code)
            return self._finish(
                surface=surface,
                task_id=task_id,
                principal_id=principal_id,
                decision=outcome.decision,
                fallback=False,
                error_code=exc.code,
                discovery=outcome,
                started=started,
                snapshots=tuple(opened),
            )
        self._log(task_id, principal_id, opened, None, started)
        return self._result(
            surface=surface,
            task_id=task_id,
            principal_id=principal_id,
            decision=outcome.decision,
            fallback=False,
            prompt_block=built.skill_block(),
            discovery=outcome,
            snapshots=tuple(opened),
            started=started,
            built=built,
        )

    def complete(self, task_id: str, *, principal_id: str) -> list[SkillSnapshot]:
        done = []
        for snapshot in self.store.for_task(task_id, principal_id=principal_id):
            if snapshot.status == RuntimePhase.ACTIVE:
                done.append(self.store.complete(snapshot.activation_id, principal_id=principal_id))
                self._release(snapshot.skill_id, principal_id)
        return done

    def cancel(self, task_id: str, *, principal_id: str) -> list[SkillSnapshot]:
        done = []
        for snapshot in self.store.for_task(task_id, principal_id=principal_id):
            if snapshot.status == RuntimePhase.ACTIVE:
                done.append(self.store.cancel(snapshot.activation_id, principal_id=principal_id))
                self._release(snapshot.skill_id, principal_id)
        return done

    def fail(self, task_id: str, *, principal_id: str, error_code: str) -> list[SkillSnapshot]:
        done = []
        for snapshot in self.store.for_task(task_id, principal_id=principal_id):
            if snapshot.status == RuntimePhase.ACTIVE:
                done.append(
                    self.store.fail(snapshot.activation_id, principal_id=principal_id, error_code=error_code)
                )
                self._release(snapshot.skill_id, principal_id)
        return done

    def _release(self, skill_id: str, principal_id: str) -> None:
        try:
            self.registry.release(skill_id, actor=SkillActor(user_id=principal_id))
        except SkillError:
            logger.info("skill release skipped skill=%s", skill_id)

    def _resources(
        self,
        snapshots: list[SkillSnapshot],
        paths: tuple[str, ...],
        actor: SkillActor,
    ) -> tuple[tuple[str, str], ...]:
        if not paths:
            return ()
        found: list[tuple[str, str]] = []
        for snapshot in snapshots:
            package = self.registry.package_dir(snapshot.skill_id, snapshot.skill_version, actor=actor)
            for relative in paths:
                text, _digest, _size = read_resource(package, relative)
                found.append((relative, text))
        return tuple(found)

    def _finish(self, **kwargs: object) -> OrchestratorResult:
        started = kwargs.pop("started")
        snapshots = kwargs.pop("snapshots", ())
        result = self._result(started=started, snapshots=snapshots, **kwargs)  # type: ignore[arg-type]
        self._log(result.task_id, result.principal_id, snapshots, result.error_code, started)  # type: ignore[arg-type]
        return result

    def _result(
        self,
        *,
        surface: Literal["chat", "agent"],
        task_id: str,
        principal_id: str,
        decision: DecisionKind,
        fallback: bool,
        started: float,
        prompt_block: str = "",
        error_code: str | None = None,
        discovery: DiscoveryOutcome | None = None,
        snapshots: tuple[SkillSnapshot, ...] = (),
        built: BuiltContext | None = None,
    ) -> OrchestratorResult:
        del built
        return OrchestratorResult(
            surface=surface,
            task_id=task_id,
            principal_id=principal_id,
            decision=decision,
            fallback=fallback,
            error_code=error_code,
            prompt_block=prompt_block,
            activation_ids=tuple(item.activation_id for item in snapshots),
            skill_ids=tuple(item.skill_id for item in snapshots),
            versions=tuple(item.skill_version for item in snapshots),
            digests=tuple(item.content_digest for item in snapshots),
            duration_ms=int((time.perf_counter() - started) * 1000),
            discovery=discovery,
        )

    def _log(
        self,
        task_id: str,
        principal_id: str,
        snapshots: tuple[SkillSnapshot, ...] | list[SkillSnapshot],
        error_code: str | None,
        started: float,
    ) -> None:
        logger.info(
            "skill runtime task=%s principal=%s skills=%s status=%s duration_ms=%s",
            task_id,
            principal_id,
            ",".join(item.skill_id for item in snapshots),
            error_code or "ok",
            int((time.perf_counter() - started) * 1000),
        )
