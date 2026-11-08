"""任务级快照。正文副本只给上下文使用，日志不写正文。"""

from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from app.skills.discovery_models import LoadedSkill
from app.skills.errors import SkillError, SkillStateError
from app.skills.runtime_state import RuntimePhase, move


class SkillSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activation_id: str
    task_id: str
    conversation_id: str | None = None
    principal_id: str
    skill_id: str
    skill_name: str
    skill_version: str
    content_digest: str
    instruction_body: str
    status: RuntimePhase
    created_at: str
    updated_at: str
    error_code: str | None = None


class ActivationStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def open(
        self,
        loaded: LoadedSkill,
        *,
        principal_id: str,
        task_id: str,
        conversation_id: str | None,
    ) -> tuple[SkillSnapshot, bool]:
        if not principal_id:
            raise SkillError("SKILL_NOT_ACCESSIBLE", "缺少用户身份")
        key = _key(principal_id, task_id, loaded.skill_id)
        with self._lock:
            current = self._read(key)
            if current is not None:
                if current.content_digest != loaded.content_digest or current.skill_version != loaded.version:
                    raise SkillError("SKILL_ACTIVATION_CONFLICT", "任务已固定另一个版本")
                return current, False
            now = _now()
            phase = RuntimePhase.CREATED
            phase = move(phase, "resolve")
            phase = move(phase, "load")
            phase = move(phase, "activate")
            snapshot = SkillSnapshot(
                activation_id="ac_" + secrets.token_hex(6),
                task_id=task_id,
                conversation_id=conversation_id,
                principal_id=principal_id,
                skill_id=loaded.skill_id,
                skill_name=loaded.name,
                skill_version=loaded.version,
                content_digest=loaded.content_digest,
                instruction_body=loaded.instruction_body,
                status=phase,
                created_at=now,
                updated_at=now,
            )
            self._write(key, snapshot)
            return snapshot, True

    def get(self, activation_id: str, *, principal_id: str) -> SkillSnapshot:
        with self._lock:
            for path in self._root.glob("*.json"):
                snapshot = self._parse(path)
                if snapshot is None or snapshot.activation_id != activation_id:
                    continue
                if snapshot.principal_id != principal_id:
                    raise SkillError("SKILL_NOT_FOUND", "技能不存在")
                return snapshot
        raise SkillError("SKILL_NOT_FOUND", "技能不存在")

    def for_task(self, task_id: str, *, principal_id: str) -> list[SkillSnapshot]:
        with self._lock:
            found = []
            for path in self._root.glob("*.json"):
                snapshot = self._parse(path)
                if snapshot is None:
                    continue
                if snapshot.principal_id == principal_id and snapshot.task_id == task_id:
                    found.append(snapshot)
            return sorted(found, key=lambda item: (item.created_at, item.skill_id))

    def complete(self, activation_id: str, *, principal_id: str) -> SkillSnapshot:
        return self._transition(activation_id, principal_id, "complete")

    def cancel(self, activation_id: str, *, principal_id: str) -> SkillSnapshot:
        return self._transition(activation_id, principal_id, "cancel", error_code="SKILL_TASK_CANCELLED")

    def fail(self, activation_id: str, *, principal_id: str, error_code: str) -> SkillSnapshot:
        return self._transition(activation_id, principal_id, "fail", error_code=error_code)

    def _transition(
        self,
        activation_id: str,
        principal_id: str,
        action: str,
        error_code: str | None = None,
    ) -> SkillSnapshot:
        with self._lock:
            snapshot = self.get(activation_id, principal_id=principal_id)
            if snapshot.status == RuntimePhase.COMPLETED and action == "complete":
                return snapshot
            if snapshot.status == RuntimePhase.CANCELLED and action == "cancel":
                return snapshot
            try:
                phase = move(snapshot.status, action)
            except SkillStateError:
                raise
            updated = snapshot.model_copy(
                update={"status": phase, "updated_at": _now(), "error_code": error_code or snapshot.error_code}
            )
            self._write(_key(updated.principal_id, updated.task_id, updated.skill_id), updated)
            return updated

    def _read(self, key: str) -> SkillSnapshot | None:
        return self._parse(self._root / f"{key}.json")

    def _parse(self, path: Path) -> SkillSnapshot | None:
        if not path.is_file():
            return None
        try:
            return SkillSnapshot.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValidationError):
            return None

    def _write(self, key: str, snapshot: SkillSnapshot) -> None:
        path = self._root / f"{key}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(snapshot.model_dump_json(), encoding="utf-8")
        os.replace(temporary, path)


def _key(principal_id: str, task_id: str, skill_id: str) -> str:
    raw = f"{principal_id}|{task_id}|{skill_id}"
    cleaned = "".join(char if char.isalnum() or char in "-_" else "_" for char in raw)
    return cleaned[:180]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
