"""PostgreSQL-backed planning repository with revision CAS and event outbox."""

from __future__ import annotations

import uuid
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import ValidationError

from app.models.planning import (
    PersistentInteraction,
    PersistentInteractionSubmission,
    PersistentPlan,
    PersistentPlanAction,
    PersistentPlanDecision,
    PersistentPlanRevision,
    PersistentPlanningEvent,
    PersistentPlanningOutbox,
)
from app.models.tables import AgentTask, ConversationMessage, ConversationSession, User
from app.planning.api_schemas import ConversationCreate, ConversationModeUpdate
from app.planning.contracts import (
    ActionType,
    Interaction,
    InteractionAction,
    InteractionPlanReview,
    InteractionPlanStep,
    InteractionStatus,
    InteractionSubmission,
    InteractionType,
    PlanDecision,
    PlanSnapshot,
    PlanStatus,
    PlanStep,
    PlanningEvent,
    PlanningEventType,
    can_transition_plan,
)
from app.planning.runtime import (
    DecisionAction,
    DecisionProposal,
    planning_transition_path,
    PlanningConflict,
    PlanningNotFound,
    PlanningOrchestrator,
    PlanningPolicyError,
    InteractionOrchestrator,
    _PLAN_REVISION_ACTIONS,
    compute_revision_changes,
)
from app.planning import revisions
from app.providers.base import LLMProvider
from app.services.prompt_service import PromptService
from app.core.config import Settings


_PLANNING_ERROR_MESSAGES = {
    "PLANNING_POLICY_REJECTED": "规划模型没有返回符合协议的结果，请重试或补充目标",
    "PLANNING_UPSTREAM_FAILED": "规划模型请求失败，请重试",
}


class SqlPlanningStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_conversation(self, owner: User, body: ConversationCreate) -> dict[str, Any]:
        now = datetime.now(UTC)
        session = ConversationSession(
            user_id=owner.id,
            title=body.title.strip(),
            mode="agent" if body.mode == "agent" else "chat",
            status="active",
            metadata_json={"workspace_mode": body.mode, "protocol_version": "1.0"},
        )
        self._session.add(session)
        await self._session.flush()
        return {
            "conversation_id": str(session.id),
            "mode": body.mode,
            "title": session.title,
            "created_at": now,
        }

    async def get_conversation(self, conversation_id: str, owner: User) -> dict[str, Any]:
        conversation = await self._conversation(conversation_id, owner.id)
        metadata = dict(conversation.metadata_json or {})
        messages = list(
            await self._session.scalars(
                select(ConversationMessage)
                .where(ConversationMessage.session_id == conversation.id)
                .order_by(ConversationMessage.created_at, ConversationMessage.id)
            )
        )
        plans = list(
            await self._session.scalars(
                select(PersistentPlan)
                .where(PersistentPlan.conversation_id == conversation.id)
                .order_by(PersistentPlan.created_at)
            )
        )
        runs = list(
            await self._session.scalars(
                select(AgentTask)
                .where(AgentTask.session_id == conversation.id, AgentTask.task_type == "agent")
                .order_by(AgentTask.created_at)
            )
        )
        return {
            "conversation_id": str(conversation.id),
            "mode": metadata.get("workspace_mode", "ask"),
            "mode_revision": int(metadata.get("mode_revision", 0)),
            "title": conversation.title,
            "created_at": conversation.created_at,
            "messages": [
                {"id": str(item.id), "role": item.role, "content": item.content, "created_at": item.created_at}
                for item in messages
            ],
            "plan_ids": [item.public_id for item in plans],
            "runs": [
                {
                    "task_id": item.id.hex,
                    "request_id": item.request_id,
                    "status": item.status,
                    **(item.output_payload or {}),
                }
                for item in runs
            ],
        }

    async def set_conversation_mode(
        self,
        conversation_id: str,
        owner: User,
        body: ConversationModeUpdate,
    ) -> dict[str, Any]:
        conversation = await self._conversation(conversation_id, owner.id, for_update=True)
        metadata = dict(conversation.metadata_json or {})
        current_revision = int(metadata.get("mode_revision", 0))
        if current_revision != body.expected_revision:
            raise PlanningConflict(f"mode revision conflict; latest revision is {current_revision}")
        metadata["workspace_mode"] = body.mode
        metadata["mode_revision"] = current_revision + 1
        conversation.metadata_json = metadata
        conversation.mode = "agent" if body.mode == "agent" else "chat"
        await self._emit_conversation(
            conversation.id,
            PlanningEventType.MODE_CHANGED,
            {"mode": body.mode, "revision": current_revision + 1},
        )
        return {"conversation_id": str(conversation.id), "mode": body.mode, "revision": current_revision + 1}

    async def create_plan(self, owner: User, conversation_id: str, goal: str) -> PlanSnapshot:
        conversation = await self._conversation(conversation_id, owner.id)
        now = datetime.now(UTC)
        public_id = f"plan_{uuid.uuid4().hex}"
        snapshot = PlanSnapshot(
            conversation_id=str(conversation.id),
            plan_id=public_id,
            revision=0,
            status=PlanStatus.UNDERSTANDING,
            goal=goal,
            created_at=now,
            updated_at=now,
        )
        row_id = uuid.uuid4()
        row = PersistentPlan(
            id=row_id,
            public_id=public_id,
            conversation_id=conversation.id,
            owner_id=owner.id,
            status=snapshot.status.value,
            current_revision=0,
        )
        self._session.add(row)
        self._session.add(PersistentPlanRevision(plan_id=row_id, revision=0, snapshot=snapshot.model_dump(mode="json")))
        await self._emit(row, PlanningEventType.PLAN_CREATED, {"revision": 0})
        await self._session.flush()
        return snapshot

    async def get_plan(self, plan_id: str, owner: User) -> PlanSnapshot:
        row = await self._owned_plan(plan_id, owner.id)
        revision = await self._current_revision(row)
        return PlanSnapshot.model_validate(revision.snapshot)

    async def get_pending_interaction(self, plan_id: str, owner: User) -> Interaction | None:
        row = await self._owned_plan(plan_id, owner.id)
        interaction = await self._session.scalar(
            select(PersistentInteraction)
            .where(
                PersistentInteraction.plan_id == row.id,
                PersistentInteraction.status == InteractionStatus.PENDING.value,
            )
            .order_by(PersistentInteraction.created_at.desc())
        )
        return Interaction.model_validate(interaction.payload) if interaction else None

    async def get_approved_revision(self, plan_id: str, owner: User) -> int | None:
        row = await self._owned_plan(plan_id, owner.id)
        revisions = await self._session.scalars(
            select(PersistentPlanRevision)
            .where(PersistentPlanRevision.plan_id == row.id)
            .order_by(PersistentPlanRevision.revision.desc())
        )
        for revision in revisions:
            snapshot = PlanSnapshot.model_validate(revision.snapshot)
            if snapshot.status == PlanStatus.APPROVED:
                return revision.revision
        return None

    async def process_message(
        self,
        plan_id: str,
        owner: User,
        expected_revision: int,
        message: str,
        provider: LLMProvider,
        settings: Settings,
    ) -> tuple[PlanSnapshot, Interaction | None, str | None]:
        row = await self._owned_plan(plan_id, owner.id)
        revision = await self._current_revision(row)
        current = PlanSnapshot.model_validate(revision.snapshot)
        if current.revision != expected_revision:
            raise PlanningConflict(f"revision conflict; latest revision is {current.revision}")
        from app.planning.runtime import PlanningDecisionEngine

        recent_messages = list(await self._session.scalars(
            select(ConversationMessage).where(ConversationMessage.session_id == row.conversation_id)
            .order_by(ConversationMessage.created_at.desc()).limit(24)
        ))
        history = [{"role": item.role, "content": str(item.content)[:8000]} for item in reversed(recent_messages)]
        engine = PlanningDecisionEngine(
            provider,
            model=settings.deepseek_model,
            timeout_seconds=settings.deepseek_timeout_seconds,
        )
        proposal = await engine.propose(goal=current.goal, user_message=message, plan=current, history=history)
        await self._settle_plan(owner, engine, plan_id, current.revision)
        InteractionOrchestrator().enforce(proposal, current)
        updated, interaction = await self.apply_proposal(plan_id, owner, current, proposal)
        conversation = await self._conversation(str(row.conversation_id), owner.id)
        self._session.add(ConversationMessage(session_id=conversation.id, role="user", content=message))
        if proposal.response:
            self._session.add(ConversationMessage(session_id=conversation.id, role="assistant", content=proposal.response))
        return updated, interaction, proposal.response or None

    async def process_message_stream(
        self,
        plan_id: str,
        owner: User,
        expected_revision: int,
        message: str,
        provider: LLMProvider,
        settings: Settings,
    ):
        """流式继续规划对话。逐段产出 SSE 帧：delta（说明文字打字机）→ plan / interaction → done。"""
        from app.core.sse import format_sse
        from app.planning.runtime import PlanningDecisionEngine

        row = await self._owned_plan(plan_id, owner.id)
        revision = await self._current_revision(row)
        current = PlanSnapshot.model_validate(revision.snapshot)
        if current.revision != expected_revision:
            raise PlanningConflict(f"revision conflict; latest revision is {current.revision}")

        recent_messages = list(await self._session.scalars(
            select(ConversationMessage).where(ConversationMessage.session_id == row.conversation_id)
            .order_by(ConversationMessage.created_at.desc()).limit(24)
        ))
        history = [{"role": item.role, "content": str(item.content)[:8000]} for item in reversed(recent_messages)]
        engine = PlanningDecisionEngine(
            provider,
            model=settings.deepseek_model,
            timeout_seconds=settings.deepseek_timeout_seconds,
        )

        proposal: DecisionProposal | None = None
        try:
            async for event, payload in engine.propose_stream(
                goal=current.goal, user_message=message, plan=current, history=history
            ):
                if event == "delta":
                    yield format_sse("delta", {"content": payload})
                elif event == "interaction_pending":
                    yield format_sse("interaction_pending", {"action": payload})
                elif event == "error":
                    yield format_sse("error", {"error": {"code": payload, "message": _PLANNING_ERROR_MESSAGES.get(payload, "规划请求失败")}})
                    return
                elif event == "done":
                    proposal = payload
        finally:
            await self._settle_plan(owner, engine, plan_id, current.revision)
        if proposal is None:
            return

        InteractionOrchestrator().enforce(proposal, current)
        updated, interaction = await self.apply_proposal(plan_id, owner, current, proposal)
        conversation = await self._conversation(str(row.conversation_id), owner.id)
        self._session.add(ConversationMessage(session_id=conversation.id, role="user", content=message))
        if proposal.response:
            self._session.add(ConversationMessage(session_id=conversation.id, role="assistant", content=proposal.response))

        yield format_sse("plan", {"plan": updated.model_dump(mode="json")})
        if interaction is not None:
            yield format_sse("interaction", {"interaction": interaction.model_dump(mode="json")})
        yield format_sse("done", {"plan": updated.model_dump(mode="json"), "response": proposal.response or ""})

    async def _settle_plan(self, owner: User, engine, plan_id: str, revision: int) -> None:
        from app.authorization.application.audit_service import QuotaService
        from app.core.context import get_request_id

        await QuotaService(self._session).settle(
            owner.id,
            source="planning",
            settlement_key=f"{get_request_id()}:{plan_id}:{revision}",
            amount=engine.total_tokens,
            kind="chat",
            model=engine._model,
            prompt_tokens=engine.prompt_tokens,
            completion_tokens=engine.completion_tokens,
            extra={"plan_id": plan_id, "revision": revision},
        )

    async def apply_proposal(
        self,
        plan_id: str,
        owner: User,
        current: PlanSnapshot,
        proposal: DecisionProposal,
    ) -> tuple[PlanSnapshot, Interaction | None]:
        row = await self._owned_plan(plan_id, owner.id, for_update=True)
        if row.current_revision != current.revision:
            raise PlanningConflict(f"revision conflict; latest revision is {row.current_revision}")
        if proposal.action == DecisionAction.READY_FOR_HANDOFF:
            raise PlanningPolicyError("handoff requires separate approval, mode switch, and authorization")
        if proposal.action == DecisionAction.RESPOND:
            # 闲聊/寒暄/提问时不进入规划流程，只自然回应。若模型借此确立了真正的目标，
            # 则记录 goal，但仍停留在理解阶段，等下一轮再开始问关键决策。
            if proposal.goal and proposal.goal.strip() and proposal.goal.strip() != current.goal.strip():
                updated = self._revise(current, goal=proposal.goal.strip())
                await self._store_revision(row, current, updated)
                await self._emit(row, PlanningEventType.PLAN_UPDATED, {"revision": updated.revision, "status": updated.status.value})
                return updated, None
            return current, None

        interaction: Interaction | None = None
        status = current.status
        changes: dict[str, Any] = {}
        if proposal.action == DecisionAction.ASK_USER:
            status = PlanStatus.CLARIFYING
            # 第一次确立真正目标时（即使是在问决策），一并把 goal 记录下来。
            if proposal.goal and proposal.goal.strip() and proposal.goal.strip() != current.goal.strip():
                changes["goal"] = proposal.goal.strip()
        elif proposal.action in {DecisionAction.UPDATE_PLAN, DecisionAction.REQUEST_APPROVAL}:
            status = PlanStatus.DRAFTING
            if proposal.action == DecisionAction.REQUEST_APPROVAL:
                status = PlanStatus.AWAITING_APPROVAL
            if not proposal.goal:
                raise PlanningPolicyError("plan proposal requires a goal")
            decisions = list(current.decisions)
            for decision in proposal.proposed_decisions:
                if decision.source == "user":
                    raise PlanningPolicyError("model cannot mark a decision as user-confirmed")
                if any(item.key == decision.key and item.source == "user" for item in decisions):
                    continue
                decisions = [item for item in decisions if item.key != decision.key]
                decisions.append(decision)
            steps = list(current.steps)
            if proposal.steps:
                try:
                    steps = [PlanStep.model_validate(step) for step in proposal.steps]
                except ValidationError:
                    raise PlanningPolicyError("planning proposal contains invalid steps") from None
            if proposal.action == DecisionAction.REQUEST_APPROVAL and not steps:
                raise PlanningPolicyError("approval requires plan steps")
            assumption_texts = proposal.assumptions or current.assumptions
            changes.update(
                goal=proposal.goal,
                assumptions=assumption_texts,
                assumption_items=revisions.sync_assumptions(current, assumption_texts),
                decisions=decisions,
                steps=steps,
            )
        else:
            raise PlanningPolicyError("unsupported planning action")

        for intermediate in planning_transition_path(current.status, status)[:-1]:
            draft = self._revise(current, status=intermediate)
            await self._store_revision(row, current, draft)
            await self._emit(row, PlanningEventType.PLAN_UPDATED, {"revision": draft.revision, "status": intermediate.value})
            current = draft
        updated = self._revise(current, status=status, **changes)
        await self._store_revision(row, current, updated)
        if proposal.action == DecisionAction.ASK_USER:
            if proposal.interaction is None:
                raise PlanningPolicyError("ask_user requires an interaction")
            interaction = Interaction.model_validate(
                {
                    **proposal.interaction.model_dump(mode="json"),
                    "protocol_version": "1.0",
                    "interaction_id": f"int_{uuid.uuid4().hex}",
                    "plan_id": plan_id,
                    "plan_revision": updated.revision,
                    "status": InteractionStatus.PENDING.value,
                }
            )
            existing = await self._session.scalars(
                select(PersistentInteraction).where(
                    PersistentInteraction.plan_id == row.id,
                    PersistentInteraction.status == InteractionStatus.PENDING.value,
                )
            )
            for previous in existing:
                previous.status = InteractionStatus.SUPERSEDED.value
                payload = dict(previous.payload)
                payload["status"] = InteractionStatus.SUPERSEDED.value
                previous.payload = payload
            self._session.add(
                PersistentInteraction(
                    public_id=interaction.interaction_id,
                    plan_id=row.id,
                    plan_revision=updated.revision,
                    status=interaction.status.value,
                    payload=interaction.model_dump(mode="json"),
                    expires_at=interaction.expires_at,
                )
            )
            await self._emit(
                row,
                PlanningEventType.INTERACTION_REQUIRED,
                {"interaction": interaction.model_dump(mode="json")},
            )
        elif proposal.action == DecisionAction.REQUEST_APPROVAL:
            interaction = Interaction(
                interaction_id=f"int_{uuid.uuid4().hex}",
                plan_id=plan_id,
                plan_revision=updated.revision,
                type=InteractionType.PLAN_REVIEW,
                prompt="请审阅并确认这份计划",
                status=InteractionStatus.PENDING,
                plan_review=InteractionPlanReview(
                    goal=updated.goal,
                    assumptions=updated.assumptions,
                    assumption_items=updated.assumption_items,
                    decisions={item.key: item.value for item in updated.decisions},
                    decision_sources={item.key: item.source for item in updated.decisions},
                    constraints=updated.constraints,
                    steps=[
                        InteractionPlanStep(id=item.id, title=item.title, summary=item.description, depends_on=item.depends_on)
                        for item in updated.steps
                    ],
                ),
            )
            await self._replace_pending_interactions(row, interaction)
            await self._emit(
                row,
                PlanningEventType.INTERACTION_REQUIRED,
                {"interaction": interaction.model_dump(mode="json")},
            )
        else:
            pending = await self._session.scalars(select(PersistentInteraction).where(
                PersistentInteraction.plan_id == row.id,
                PersistentInteraction.status == InteractionStatus.PENDING.value,
            ))
            for previous in pending:
                previous.status = InteractionStatus.SUPERSEDED.value
                previous.payload = {**previous.payload, "status": InteractionStatus.SUPERSEDED.value}
            await self._emit(row, PlanningEventType.PLAN_UPDATED, {"revision": updated.revision, "status": updated.status.value})
        return updated, interaction

    async def submit_interaction(
        self,
        plan_id: str,
        owner: User,
        submission: InteractionSubmission,
    ) -> PlanSnapshot:
        row = await self._owned_plan(plan_id, owner.id, for_update=True)
        existing_submission = await self._session.scalar(
            select(PersistentInteractionSubmission).where(
                PersistentInteractionSubmission.plan_id == row.id,
                PersistentInteractionSubmission.client_submission_id == submission.client_submission_id,
            )
        )
        if existing_submission is not None:
            if (
                existing_submission.interaction_id != await self._interaction_db_id(row.id, submission.interaction_id)
                or existing_submission.value != submission.value
            ):
                raise PlanningConflict("idempotency key was reused with a different submission")
            return PlanSnapshot.model_validate(existing_submission.result_snapshot)

        current = PlanSnapshot.model_validate((await self._current_revision(row)).snapshot)
        if current.revision != submission.expected_plan_revision:
            raise PlanningConflict(f"revision conflict; latest revision is {current.revision}")
        interaction_row = await self._session.scalar(
            select(PersistentInteraction)
            .where(
                PersistentInteraction.plan_id == row.id,
                PersistentInteraction.public_id == submission.interaction_id,
            )
            .with_for_update()
        )
        if interaction_row is None:
            raise PlanningNotFound("pending interaction not found")
        if interaction_row.status != InteractionStatus.PENDING.value:
            raise PlanningConflict("interaction is no longer pending")
        if interaction_row.plan_revision != current.revision:
            raise PlanningConflict("interaction belongs to a stale plan revision")
        if interaction_row.expires_at and interaction_row.expires_at <= datetime.now(UTC):
            raise PlanningConflict("interaction has expired")
        interaction = Interaction.model_validate(interaction_row.payload)
        value = PlanningOrchestrator._validate_submission_value(interaction, submission.value)
        decisions = [item for item in current.decisions if item.key != self._decision_key(interaction)]
        decisions.append(
            PlanDecision(
                key=self._decision_key(interaction),
                value=value,
                source="user",
                confidence=1.0,
            )
        )
        updated = self._revise(current, status=PlanStatus.DRAFTING, decisions=decisions)
        await self._store_revision(row, current, updated)
        interaction_row.status = InteractionStatus.SUBMITTED.value
        payload = dict(interaction_row.payload)
        payload["status"] = InteractionStatus.SUBMITTED.value
        interaction_row.payload = payload
        self._session.add(
            PersistentInteractionSubmission(
                plan_id=row.id,
                interaction_id=interaction_row.id,
                owner_id=owner.id,
                client_submission_id=submission.client_submission_id,
                expected_revision=submission.expected_plan_revision,
                value=value,
                result_snapshot=updated.model_dump(mode="json"),
            )
        )
        conversation = await self._conversation(str(row.conversation_id), owner.id)
        if interaction.type == InteractionType.FORM and isinstance(value, dict):
            field_labels = {field.key: field.label for field in (interaction.form or [])}
            answer_text = "; ".join(f"{field_labels.get(key, key)}: {val}" for key, val in value.items())
        else:
            selected = value if isinstance(value, list) else [value]
            answer_labels = [option.label + (f" — {option.description}" if option.description else "")
                             for option in interaction.options if option.id in selected]
            answer_text = "; ".join(answer_labels) if answer_labels else json.dumps(value, ensure_ascii=False)
        self._session.add(
            ConversationMessage(
                session_id=conversation.id,
                role="user",
                content=f"[Planning answer] {interaction.prompt}: {answer_text}",
            )
        )
        await self._emit(row, PlanningEventType.INTERACTION_SUBMITTED, {"interaction_id": interaction.interaction_id, "revision": updated.revision})
        await self._emit(row, PlanningEventType.PLAN_UPDATED, {"revision": updated.revision})
        return updated

    async def skip_interaction(
        self, plan_id: str, interaction_id: str, owner: User, expected_revision: int
    ) -> PlanSnapshot:
        row = await self._owned_plan(plan_id, owner.id, for_update=True)
        current = PlanSnapshot.model_validate((await self._current_revision(row)).snapshot)
        if current.revision != expected_revision:
            raise PlanningConflict(f"revision conflict; latest revision is {current.revision}")
        interaction = await self._session.scalar(
            select(PersistentInteraction)
            .where(
                PersistentInteraction.plan_id == row.id,
                PersistentInteraction.public_id == interaction_id,
            )
            .with_for_update()
        )
        if interaction is None:
            raise PlanningNotFound("pending interaction not found")
        if interaction.status != InteractionStatus.PENDING.value or interaction.plan_revision != current.revision:
            raise PlanningConflict("interaction is no longer pending")
        interaction.status = InteractionStatus.SKIPPED.value
        interaction.payload = {**interaction.payload, "status": InteractionStatus.SKIPPED.value}
        interaction_contract = Interaction.model_validate(interaction.payload)
        conversation = await self._conversation(str(row.conversation_id), owner.id)
        self._session.add(
            ConversationMessage(
                session_id=conversation.id,
                role="user",
                content=f"[Planning answer] Skipped: {interaction_contract.prompt}",
            )
        )
        updated = self._revise(current, status=PlanStatus.DRAFTING)
        await self._store_revision(row, current, updated)
        await self._emit(row, PlanningEventType.INTERACTION_SKIPPED, {"interaction_id": interaction_id})
        await self._emit(row, PlanningEventType.PLAN_UPDATED, {"revision": updated.revision})
        return updated

    async def apply_action(self, plan_id: str, owner: User, action: InteractionAction) -> tuple[PlanSnapshot, dict[str, Any]]:
        """统一交互动作入口（持久化版），返回 (计划快照, 影响分析结果)。

        校验链：归属（加锁）→ 幂等 → 版本 CAS → 动作白名单（契约层）→ 目标定位 → 分发。
        submit 委托 submit_interaction（其内部再校验卡片与 interaction_submissions 幂等），
        假设/步骤/目标/约束修订走 revisions 纯函数 + _store_revision 落新版本。
        """
        row = await self._owned_plan(plan_id, owner.id, for_update=True)

        existing = await self._session.scalar(
            select(PersistentPlanAction).where(
                PersistentPlanAction.plan_id == row.id,
                PersistentPlanAction.idempotency_key == action.idempotency_key,
            )
        )
        if existing is not None:
            if (
                existing.action_type != action.action_type.value
                or existing.target_type != action.target_type.value
                or existing.target_id != action.target_id
                or existing.interaction_id != action.interaction_id
            ):
                raise PlanningConflict("idempotency key was reused with a different action")
            return PlanSnapshot.model_validate(existing.result_snapshot), (existing.payload.get("affected") if isinstance(existing.payload, dict) else {})

        current = PlanSnapshot.model_validate((await self._current_revision(row)).snapshot)
        if current.revision != action.plan_revision:
            raise PlanningConflict(f"revision conflict; latest revision is {current.revision}")

        if action.action_type == ActionType.SUBMIT:
            updated = await self.submit_interaction(
                plan_id,
                owner,
                InteractionSubmission(
                    interaction_id=action.interaction_id or "",
                    expected_plan_revision=action.plan_revision,
                    client_submission_id=action.idempotency_key,
                    value=action.payload.get("value"),
                ),
            )
            affected: dict[str, Any] = {}
        elif action.action_type == ActionType.SKIP:
            updated = await self.skip_interaction(
                plan_id, action.interaction_id or "", owner, action.plan_revision
            )
            affected = {}
        elif action.action_type in _PLAN_REVISION_ACTIONS:
            try:
                changes, affected = compute_revision_changes(current, action)
            except (ValueError, LookupError) as exc:
                raise PlanningPolicyError(str(exc)) from None
            # 实时就地修订：只改内容、保留当前状态（审阅卡等待审批时继续等待审批，
            # 用户改完即可直接“确认计划”）。不强制打回 drafting。
            updated = self._revise(current, **changes)
            await self._store_revision(row, current, updated)
            await self._emit(
                row,
                PlanningEventType.PLAN_UPDATED,
                {"revision": updated.revision, "action": action.action_type.value, "affected": affected},
            )
        elif action.action_type == ActionType.REVISION_ROLLBACK:
            try:
                target_revision = int(action.target_id or "")
            except ValueError as exc:
                raise PlanningPolicyError("revision_rollback requires a numeric revision") from None
            updated = await self._rollback_to_revision(row, owner, current, target_revision)
            affected = {"rolled_back_to": target_revision}
        else:
            raise PlanningPolicyError(f"action not implemented in this milestone: {action.action_type.value}")

        self._session.add(
            PersistentPlanAction(
                plan_id=row.id,
                owner_id=owner.id,
                idempotency_key=action.idempotency_key,
                action_type=action.action_type.value,
                target_type=action.target_type.value,
                target_id=action.target_id,
                interaction_id=action.interaction_id,
                payload={**action.payload, "affected": affected},
                result_revision=updated.revision,
                result_snapshot=updated.model_dump(mode="json"),
            )
        )
        return updated, affected

    async def _rollback_to_revision(
        self, row: PersistentPlan, owner: User, current: PlanSnapshot, target_revision: int
    ) -> PlanSnapshot:
        if target_revision >= current.revision:
            raise PlanningConflict("cannot roll back to the current or a future revision")
        target_row = await self._session.scalar(
            select(PersistentPlanRevision).where(
                PersistentPlanRevision.plan_id == row.id,
                PersistentPlanRevision.revision == target_revision,
            )
        )
        if target_row is None:
            raise PlanningNotFound("target revision not found")
        target = PlanSnapshot.model_validate(target_row.snapshot)
        updated = revisions.rollback_snapshot(current, target)
        await self._store_revision(row, current, updated)
        await self._emit(
            row,
            PlanningEventType.PLAN_UPDATED,
            {"revision": updated.revision, "action": "revision_rollback", "rolled_back_to": target_revision},
        )
        return updated

    async def list_revisions(self, plan_id: str, owner: User) -> list[dict[str, Any]]:
        row = await self._owned_plan(plan_id, owner.id)
        rows = await self._session.scalars(
            select(PersistentPlanRevision)
            .where(PersistentPlanRevision.plan_id == row.id)
            .order_by(PersistentPlanRevision.revision.desc())
        )
        return [
            {
                "revision": item.revision,
                "status": PlanSnapshot.model_validate(item.snapshot).status.value,
                "goal": PlanSnapshot.model_validate(item.snapshot).goal,
                "created_at": item.created_at.isoformat(),
            }
            for item in rows
        ]

    async def get_revision_snapshot(self, plan_id: str, owner: User, revision: int) -> PlanSnapshot:
        row = await self._owned_plan(plan_id, owner.id)
        target = await self._session.scalar(
            select(PersistentPlanRevision).where(
                PersistentPlanRevision.plan_id == row.id,
                PersistentPlanRevision.revision == revision,
            )
        )
        if target is None:
            raise PlanningNotFound("revision not found")
        return PlanSnapshot.model_validate(target.snapshot)

    async def approve(self, plan_id: str, owner: User, expected_revision: int) -> PlanSnapshot:
        return await self._transition(plan_id, owner, expected_revision, PlanStatus.APPROVED, PlanningEventType.PLAN_APPROVED, expected=PlanStatus.AWAITING_APPROVAL)

    async def switch_mode(self, plan_id: str, owner: User, expected_revision: int, target_mode: str) -> PlanSnapshot:
        if target_mode != "agent":
            raise PlanningPolicyError("plan handoff mode switch must target agent")
        row = await self._owned_plan(plan_id, owner.id, for_update=True)
        current = PlanSnapshot.model_validate((await self._current_revision(row)).snapshot)
        if current.revision != expected_revision:
            raise PlanningConflict(f"revision conflict; latest revision is {current.revision}")
        if current.status != PlanStatus.APPROVED:
            raise PlanningConflict("only an approved plan can switch to Agent mode")
        conversation = await self._conversation(str(row.conversation_id), owner.id, for_update=True)
        metadata = dict(conversation.metadata_json or {})
        metadata["workspace_mode"] = "agent"
        metadata["mode_revision"] = int(metadata.get("mode_revision", 0)) + 1
        conversation.metadata_json = metadata
        conversation.mode = "agent"
        updated = self._revise(current, status=PlanStatus.READY_FOR_HANDOFF)
        await self._store_revision(row, current, updated)
        await self._emit(row, PlanningEventType.MODE_CHANGED, {"mode": "agent", "revision": updated.revision})
        return updated

    async def authorize_execution(
        self,
        plan_id: str,
        owner: User,
        *,
        approved_revision: int,
        expected_revision: int,
        authorization_id: str,
    ) -> PlanSnapshot:
        if not authorization_id or len(authorization_id) > 128:
            raise PlanningPolicyError("execution authorization id is required")
        row = await self._owned_plan(plan_id, owner.id, for_update=True)
        current = PlanSnapshot.model_validate((await self._current_revision(row)).snapshot)
        if current.revision != expected_revision:
            raise PlanningConflict(f"revision conflict; latest revision is {current.revision}")
        approved = await self._session.scalar(
            select(PersistentPlanRevision).where(
                PersistentPlanRevision.plan_id == row.id,
                PersistentPlanRevision.revision == approved_revision,
            )
        )
        if approved is None or PlanSnapshot.model_validate(approved.snapshot).status != PlanStatus.APPROVED:
            raise PlanningConflict("authorization does not reference an approved plan revision")
        if current.status != PlanStatus.READY_FOR_HANDOFF:
            raise PlanningConflict("switching mode does not authorize execution")
        updated = self._revise(current, status=PlanStatus.EXECUTING)
        await self._store_revision(row, current, updated)
        await self._emit(row, PlanningEventType.RUN_STARTED, {"revision": updated.revision, "authorization_id": authorization_id})
        return updated

    async def events_after(self, plan_id: str, owner: User, after: int) -> tuple[list[PlanningEvent], int]:
        if after < 0:
            raise ValueError("after must be non-negative")
        row = await self._owned_plan(plan_id, owner.id)
        rows = list(
            await self._session.scalars(
                select(PersistentPlanningEvent)
                .where(
                    PersistentPlanningEvent.conversation_id == row.conversation_id,
                    PersistentPlanningEvent.sequence > after,
                )
                .order_by(PersistentPlanningEvent.sequence)
            )
        )
        events = [
            PlanningEvent(
                event_id=item.event_id,
                sequence=item.sequence,
                conversation_id=str(item.conversation_id),
                plan_id=(await self._event_plan_public_id(item.plan_id)) if item.plan_id else None,
                type=item.event_type,
                payload=item.payload,
                occurred_at=item.occurred_at,
            )
            for item in rows
        ]
        next_sequence = rows[-1].sequence if rows else after
        return events, next_sequence

    async def conversation_events_after(
        self, conversation_id: str, owner: User, after: int
    ) -> tuple[list[PlanningEvent], int]:
        if after < 0:
            raise ValueError("after must be non-negative")
        conversation = await self._conversation(conversation_id, owner.id)
        rows = list(
            await self._session.scalars(
                select(PersistentPlanningEvent)
                .where(
                    PersistentPlanningEvent.conversation_id == conversation.id,
                    PersistentPlanningEvent.sequence > after,
                )
                .order_by(PersistentPlanningEvent.sequence)
            )
        )
        events = [
            PlanningEvent(
                event_id=item.event_id,
                sequence=item.sequence,
                conversation_id=str(item.conversation_id),
                plan_id=(await self._event_plan_public_id(item.plan_id)) if item.plan_id else None,
                type=item.event_type,
                payload=item.payload,
                occurred_at=item.occurred_at,
            )
            for item in rows
        ]
        return events, rows[-1].sequence if rows else after

    async def _transition(
        self,
        plan_id: str,
        owner: User,
        expected_revision: int,
        target: PlanStatus,
        event_type: PlanningEventType,
        *,
        expected: PlanStatus,
    ) -> PlanSnapshot:
        row = await self._owned_plan(plan_id, owner.id, for_update=True)
        current = PlanSnapshot.model_validate((await self._current_revision(row)).snapshot)
        if current.revision != expected_revision:
            raise PlanningConflict(f"revision conflict; latest revision is {current.revision}")
        if current.status != expected:
            raise PlanningConflict(f"plan must be {expected.value} before {target.value}")
        updated = self._revise(current, status=target)
        await self._store_revision(row, current, updated)
        if target == PlanStatus.APPROVED:
            pending = await self._session.scalars(
                select(PersistentInteraction).where(
                    PersistentInteraction.plan_id == row.id,
                    PersistentInteraction.status == InteractionStatus.PENDING.value,
                ).with_for_update()
            )
            for item in pending:
                item.status = InteractionStatus.SUBMITTED.value
                item.payload = {**item.payload, "status": InteractionStatus.SUBMITTED.value}
        conversation = await self._conversation(str(row.conversation_id), owner.id)
        self._session.add(
            ConversationMessage(
                session_id=conversation.id,
                role="user",
                content=f"[Planning approval] Confirmed plan revision {updated.revision}.",
            )
        )
        await self._emit(row, event_type, {"revision": updated.revision})
        return updated

    async def _replace_pending_interactions(self, row: PersistentPlan, interaction: Interaction) -> None:
        existing = await self._session.scalars(
            select(PersistentInteraction).where(
                PersistentInteraction.plan_id == row.id,
                PersistentInteraction.status == InteractionStatus.PENDING.value,
            ).with_for_update()
        )
        for previous in existing:
            previous.status = InteractionStatus.SUPERSEDED.value
            previous.payload = {**previous.payload, "status": InteractionStatus.SUPERSEDED.value}
        self._session.add(
            PersistentInteraction(
                public_id=interaction.interaction_id,
                plan_id=row.id,
                plan_revision=interaction.plan_revision,
                status=interaction.status.value,
                payload=interaction.model_dump(mode="json"),
                expires_at=interaction.expires_at,
            )
        )

    async def _store_revision(self, row: PersistentPlan, current: PlanSnapshot, updated: PlanSnapshot) -> None:
        result = await self._session.execute(
            update(PersistentPlan)
            .where(
                PersistentPlan.id == row.id,
                PersistentPlan.owner_id == row.owner_id,
                PersistentPlan.current_revision == current.revision,
            )
            .values(current_revision=updated.revision, status=updated.status.value, updated_at=updated.updated_at)
        )
        if result.rowcount != 1:
            raise PlanningConflict("plan revision changed concurrently")
        self._session.add(
            PersistentPlanRevision(
                plan_id=row.id,
                revision=updated.revision,
                snapshot=updated.model_dump(mode="json"),
            )
        )
        previous = await self._session.scalars(
            select(PersistentPlanDecision).where(
                PersistentPlanDecision.plan_id == row.id,
                PersistentPlanDecision.active.is_(True),
            )
        )
        for decision in previous:
            decision.active = False
        for decision in updated.decisions:
            self._session.add(
                PersistentPlanDecision(
                    plan_id=row.id,
                    revision=updated.revision,
                    decision_key=decision.key,
                    value=decision.value,
                    source=decision.source,
                    confidence=decision.confidence,
                    dependencies=decision.depends_on,
                    active=True,
                )
            )
        await self._session.flush()

    async def _emit(self, plan: PersistentPlan, event_type: PlanningEventType, payload: dict[str, Any]) -> None:
        await self._session.scalar(
            select(ConversationSession.id)
            .where(ConversationSession.id == plan.conversation_id)
            .with_for_update()
        )
        sequence = await self._session.scalar(
            select(func.coalesce(func.max(PersistentPlanningEvent.sequence), 0)).where(
                PersistentPlanningEvent.conversation_id == plan.conversation_id
            )
        )
        event_id = f"evt_{uuid.uuid4().hex}"
        event = PersistentPlanningEvent(
            event_id=event_id,
            plan_id=plan.id,
            conversation_id=plan.conversation_id,
            sequence=int(sequence or 0) + 1,
            event_type=event_type.value,
            payload=payload,
            occurred_at=datetime.now(UTC),
        )
        self._session.add(event)
        await self._session.flush()
        self._session.add(PersistentPlanningOutbox(event_id=event_id, attempts=0))

    async def _emit_conversation(
        self, conversation_id: uuid.UUID, event_type: PlanningEventType, payload: dict[str, Any]
    ) -> None:
        sequence = await self._session.scalar(
            select(func.coalesce(func.max(PersistentPlanningEvent.sequence), 0)).where(
                PersistentPlanningEvent.conversation_id == conversation_id
            )
        )
        event_id = f"evt_{uuid.uuid4().hex}"
        event = PersistentPlanningEvent(
            event_id=event_id,
            plan_id=None,
            conversation_id=conversation_id,
            sequence=int(sequence or 0) + 1,
            event_type=event_type.value,
            payload=payload,
            occurred_at=datetime.now(UTC),
        )
        self._session.add(event)
        await self._session.flush()
        self._session.add(PersistentPlanningOutbox(event_id=event_id, attempts=0))

    async def _event_plan_public_id(self, plan_db_id: uuid.UUID) -> str | None:
        plan = await self._session.scalar(select(PersistentPlan).where(PersistentPlan.id == plan_db_id))
        return plan.public_id if plan else None

    async def _owned_plan(self, plan_id: str, owner_id: uuid.UUID, *, for_update: bool = False) -> PersistentPlan:
        statement = (
            select(PersistentPlan)
            .join(ConversationSession, PersistentPlan.conversation_id == ConversationSession.id)
            .where(
                PersistentPlan.public_id == plan_id,
                PersistentPlan.owner_id == owner_id,
                ConversationSession.user_id == owner_id,
                ConversationSession.status == "active",
            )
        )
        if for_update:
            statement = statement.with_for_update()
        row = await self._session.scalar(statement)
        if row is None:
            raise PlanningNotFound("plan not found")
        return row

    async def _conversation(self, public_id: str, owner_id: uuid.UUID, *, for_update: bool = False) -> ConversationSession:
        try:
            parsed = uuid.UUID(public_id)
        except (ValueError, AttributeError):
            raise PlanningNotFound("conversation not found") from None
        statement = select(ConversationSession).where(
            ConversationSession.id == parsed,
            ConversationSession.user_id == owner_id,
            ConversationSession.status == "active",
        )
        if for_update:
            statement = statement.with_for_update()
        conversation = await self._session.scalar(statement)
        if conversation is None:
            raise PlanningNotFound("conversation not found")
        return conversation

    async def _current_revision(self, row: PersistentPlan) -> PersistentPlanRevision:
        revision = await self._session.scalar(
            select(PersistentPlanRevision).where(
                PersistentPlanRevision.plan_id == row.id,
                PersistentPlanRevision.revision == row.current_revision,
            )
        )
        if revision is None:
            raise PlanningNotFound("plan revision not found")
        return revision

    async def _interaction_db_id(self, plan_id: uuid.UUID, interaction_public_id: str) -> uuid.UUID | None:
        interaction = await self._session.scalar(
            select(PersistentInteraction).where(
                PersistentInteraction.plan_id == plan_id,
                PersistentInteraction.public_id == interaction_public_id,
            )
        )
        return interaction.id if interaction else None

    @staticmethod
    def _decision_key(interaction: Interaction) -> str:
        explicit = interaction.constraints.get("decision_key")
        if explicit and isinstance(explicit, str) and explicit.strip():
            return explicit.strip()[:128]
        # Fallback: derive a readable key from the prompt instead of leaking the
        # internal interaction_id to the review card.
        prompt = (interaction.prompt or "").strip()
        cleaned = "".join(ch for ch in prompt if ch.isalnum() or ch in " _-")
        cleaned = " ".join(cleaned.split())
        return (cleaned[:32] or interaction.interaction_id)[:128]

    @staticmethod
    def _revise(current: PlanSnapshot, **changes: Any) -> PlanSnapshot:
        data = current.model_dump()
        data.update({"revision": current.revision + 1, "updated_at": datetime.now(UTC), **changes})
        return PlanSnapshot.model_validate(data)
