"""确定性计划修订纯函数（M3 假设 / M4 步骤 / M6 版本 diff）。

这些函数只操作内存中的 PlanSnapshot 字段，返回字段变更 dict（供
`PlanningOrchestrator._revise` / `SqlPlanningStore._revise` 落库）与影响分析
结果，不执行持久化、授权或状态机迁移。目标是让"编辑假设 / 拆分步骤 / 排序 /
对比版本"成为可校验、可审计、可复用的业务逻辑，而不是压在 LLM 的自由发挥上。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from app.planning.contracts import Assumption, AssumptionStatus, PlanSnapshot, PlanStatus, PlanStep

# M3 影响分析用的停用词：从假设文本中剔除后做关键词匹配，降低误报。
_STOPWORDS = frozenset(
    {
        "use", "using", "the", "a", "an", "on", "in", "with", "for", "of", "to",
        "and", "or", "will", "we", "our", "be", "is", "are", "that", "this",
        "deploy", "deployment", "single", "multiple", "setup", "set", "up", "it",
        "as", "at", "by", "from", "should", "can", "could", "would",
    }
)


def _keywords(text: str) -> list[str]:
    return [word for word in re.findall(r"[A-Za-z0-9]+", text.lower()) if len(word) >= 3 and word not in _STOPWORDS]


# ---------------------------------------------------------------------------
# 结构化假设
# ---------------------------------------------------------------------------

def _unused_assumption_id(plan: PlanSnapshot, taken: set[str]) -> str:
    n = 0
    while True:
        candidate = f"asm_{n}"
        if candidate not in taken and all(item.id != candidate for item in plan.assumption_items):
            return candidate
        n += 1


def sync_assumptions(plan: PlanSnapshot, texts: list[str]) -> list[Assumption]:
    """把纯文本假设映射为结构化假设；文本不变时保留稳定 id 与状态。

    历史数据（仅有 assumptions，无 assumption_items）首次进入时按序分配 asm_0..n。
    """
    existing = {item.text: item for item in plan.assumption_items}
    result: list[Assumption] = []
    taken: set[str] = set()
    for raw in texts:
        text = str(raw).strip()
        if not text:
            continue
        prior = existing.get(text)
        if prior is not None:
            result.append(prior)
            taken.add(prior.id)
            continue
        new_id = _unused_assumption_id(plan, taken)
        result.append(Assumption(id=new_id, text=text))
        taken.add(new_id)
    return result


def _assumption_index(plan: PlanSnapshot, assumption_id: str) -> int | None:
    for idx, item in enumerate(plan.assumption_items):
        if item.id == assumption_id:
            return idx
    return None


def _find_affected(plan: PlanSnapshot, assumption_id: str, old_text: str) -> tuple[list[str], list[str]]:
    """定位依赖某假设的决策键与步骤 id（关键词文本匹配）。

    depends_on 保留为步骤间顺序依赖语义（供 M4 排序校验），假设影响分析不与之
    复用；改用关键词匹配步骤标题/描述与决策值（假设常为技术名词，如 "PostgreSQL"）。
    """
    affected_decisions: list[str] = []
    affected_steps: list[str] = []
    keywords = _keywords(old_text)
    if not keywords:
        return affected_decisions, affected_steps

    def any_keyword(haystack: str) -> bool:
        lowered = haystack.lower()
        return any(keyword in lowered for keyword in keywords)

    for decision in plan.decisions:
        if _value_mentions(decision.value, keywords):
            affected_decisions.append(decision.key)
    for step in plan.steps:
        if any_keyword(step.title or "") or any_keyword(step.description or ""):
            affected_steps.append(step.id)
    return affected_decisions, affected_steps


def _value_mentions(value: Any, keywords: list[str]) -> bool:
    if isinstance(value, str):
        lowered = value.lower()
        return any(keyword in lowered for keyword in keywords)
    if isinstance(value, (list, tuple)):
        return any(_value_mentions(item, keywords) for item in value)
    if isinstance(value, dict):
        return any(_value_mentions(item, keywords) for item in value.values())
    return False


def edit_assumption(
    plan: PlanSnapshot, assumption_id: str, new_text: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """编辑假设文本：更新文本、标记 confirmed，返回字段变更与影响分析。"""
    idx = _assumption_index(plan, assumption_id)
    if idx is None:
        raise LookupError(f"assumption not found: {assumption_id}")
    old_text = plan.assumption_items[idx].text
    items = list(plan.assumption_items)
    items[idx] = items[idx].model_copy(update={"text": new_text.strip(), "status": AssumptionStatus.CONFIRMED})
    decisions, steps = _find_affected(plan, assumption_id, old_text)
    return _assumption_changes(items), _affected_payload(assumption_id, decisions, steps)


def confirm_assumption(plan: PlanSnapshot, assumption_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """确认假设：仅标记 confirmed，不改文本。"""
    idx = _assumption_index(plan, assumption_id)
    if idx is None:
        raise LookupError(f"assumption not found: {assumption_id}")
    items = list(plan.assumption_items)
    items[idx] = items[idx].model_copy(update={"status": AssumptionStatus.CONFIRMED})
    return _assumption_changes(items), _affected_payload(assumption_id, [], [])


def reject_assumption(
    plan: PlanSnapshot, assumption_id: str, replacement: str | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """否定假设：标记 rejected；若提供 replacement 则替换文本并标记 confirmed。"""
    idx = _assumption_index(plan, assumption_id)
    if idx is None:
        raise LookupError(f"assumption not found: {assumption_id}")
    old_text = plan.assumption_items[idx].text
    items = list(plan.assumption_items)
    if replacement and replacement.strip():
        items[idx] = items[idx].model_copy(update={"text": replacement.strip(), "status": AssumptionStatus.CONFIRMED})
    else:
        items[idx] = items[idx].model_copy(update={"status": AssumptionStatus.REJECTED})
    decisions, steps = _find_affected(plan, assumption_id, old_text)
    return _assumption_changes(items), _affected_payload(assumption_id, decisions, steps)


def _assumption_changes(items: list[Assumption]) -> dict[str, Any]:
    return {
        "assumption_items": items,
        "assumptions": [item.text for item in items if item.status != AssumptionStatus.REJECTED],
    }


def _affected_payload(assumption_id: str, decisions: list[str], steps: list[str]) -> dict[str, Any]:
    return {"assumption_id": assumption_id, "affected_decisions": decisions, "affected_steps": steps}


# ---------------------------------------------------------------------------
# 步骤修订（M4）
# ---------------------------------------------------------------------------

def _step_index(plan: PlanSnapshot, step_id: str) -> int | None:
    for idx, step in enumerate(plan.steps):
        if step.id == step_id:
            return idx
    return None


def validate_step_dependencies(plan: PlanSnapshot, steps: list[PlanStep]) -> None:
    """校验依赖：depends_on 引用的步骤必须在列表中，且不能成环。"""
    ids = {step.id for step in steps}
    for step in steps:
        for dep in step.depends_on:
            if dep not in ids:
                raise ValueError(f"step {step.id} depends on unknown step {dep}")
    # 拓扑检测成环。
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(step_id: str) -> None:
        if step_id in visited:
            return
        if step_id in visiting:
            raise ValueError(f"step dependency cycle detected at {step_id}")
        visiting.add(step_id)
        step = next((item for item in steps if item.id == step_id), None)
        for dep in (step.depends_on if step else []):
            visit(dep)
        visiting.discard(step_id)
        visited.add(step_id)

    for step in steps:
        visit(step.id)


def validate_step_order(steps: list[PlanStep]) -> None:
    """校验顺序：被依赖步骤必须排在依赖它的步骤之前。"""
    positions = {step.id: idx for idx, step in enumerate(steps)}
    for idx, step in enumerate(steps):
        for dep in step.depends_on:
            if positions.get(dep, -1) > idx:
                raise ValueError(f"step {step.id} must come after {dep}")


def reorder_steps(plan: PlanSnapshot, ordered_ids: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """按给定 id 顺序重排步骤，校验依赖顺序。"""
    by_id = {step.id: step for step in plan.steps}
    if set(ordered_ids) != set(by_id.keys()):
        raise ValueError("step order must contain exactly the current step ids")
    steps = [by_id[step_id] for step_id in ordered_ids]
    validate_step_order(steps)
    return {"steps": steps}, {"reordered": ordered_ids}


def add_step(plan: PlanSnapshot, step: PlanStep) -> tuple[dict[str, Any], dict[str, Any]]:
    steps = list(plan.steps)
    if any(item.id == step.id for item in steps):
        raise ValueError(f"step {step.id} already exists")
    steps.append(step)
    validate_step_dependencies(plan, steps)
    return {"steps": steps}, {"added": step.id}


def edit_step(plan: PlanSnapshot, step: PlanStep) -> tuple[dict[str, Any], dict[str, Any]]:
    idx = _step_index(plan, step.id)
    if idx is None:
        raise LookupError(f"step not found: {step.id}")
    steps = list(plan.steps)
    steps[idx] = step
    validate_step_dependencies(plan, steps)
    return {"steps": steps}, {"edited": step.id}


def remove_step(plan: PlanSnapshot, step_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    idx = _step_index(plan, step_id)
    if idx is None:
        raise LookupError(f"step not found: {step_id}")
    steps = [
        item.model_copy(update={"depends_on": [d for d in item.depends_on if d != step_id]})
        for item in plan.steps
        if item.id != step_id
    ]
    return {"steps": steps}, {"removed": step_id}


def split_step(
    plan: PlanSnapshot, step_id: str, children: list[PlanStep]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """拆分步骤：用 children 替换原步骤，children 继承原步骤的被依赖关系。"""
    idx = _step_index(plan, step_id)
    if idx is None:
        raise LookupError(f"step not found: {step_id}")
    original = plan.steps[idx]
    if not children:
        raise ValueError("split requires at least one child step")
    # 原步骤的依赖者改指向第一个子步骤（子步骤之间默认按顺序依赖）。
    first_child_id = children[0].id
    rebuilt: list[PlanStep] = []
    for item in plan.steps:
        if item.id == step_id:
            continue
        rebuilt.append(
            item.model_copy(
                update={
                    "depends_on": [first_child_id if d == step_id else d for d in item.depends_on]
                }
            )
        )
    steps = rebuilt[:idx] + children + rebuilt[idx:]
    validate_step_dependencies(plan, steps)
    return {"steps": steps}, {"split": step_id, "children": [item.id for item in children]}


def merge_steps(plan: PlanSnapshot, step_ids: list[str], merged: PlanStep) -> tuple[dict[str, Any], dict[str, Any]]:
    """合并多个步骤为一个。"""
    if len(step_ids) < 2:
        raise ValueError("merge requires at least two step ids")
    if any(_step_index(plan, step_id) is None for step_id in step_ids):
        raise LookupError("merge step not found")
    idx = min(_step_index(plan, step_id) for step_id in step_ids)  # type: ignore[arg-type]
    kept = [item for item in plan.steps if item.id not in step_ids]
    # 被合并步骤的依赖者改指向合并后步骤。
    rebuilt: list[PlanStep] = []
    for item in kept:
        rebuilt.append(
            item.model_copy(
                update={
                    "depends_on": [
                        merged.id if d in step_ids else d for d in item.depends_on
                    ]
                }
            )
        )
    steps = rebuilt[:idx] + [merged] + rebuilt[idx:]
    validate_step_dependencies(plan, steps)
    return {"steps": steps}, {"merged": step_ids, "into": merged.id}


# ---------------------------------------------------------------------------
# 版本 diff（M6）
# ---------------------------------------------------------------------------

def rollback_snapshot(current: PlanSnapshot, target: PlanSnapshot) -> PlanSnapshot:
    """把目标历史版本复制为当前版本之后的派生版本，回到草稿态。"""
    data = target.model_dump(exclude={"revision", "created_at", "updated_at", "status"})
    return PlanSnapshot(
        **data,
        revision=current.revision + 1,
        status=PlanStatus.DRAFTING,
        created_at=target.created_at,
        updated_at=datetime.now(UTC),
    )


def compute_plan_diff(old: PlanSnapshot, new: PlanSnapshot) -> dict[str, Any]:
    """计算两个版本快照之间的可展示差异。"""
    return {
        "goal": {"before": old.goal, "after": new.goal},
        "status": {"before": old.status.value, "after": new.status.value},
        "assumptions": {
            "before": old.assumptions,
            "after": new.assumptions,
        },
        "decisions": {
            "before": [item.model_dump(mode="json") for item in old.decisions],
            "after": [item.model_dump(mode="json") for item in new.decisions],
        },
        "steps": {
            "before": [item.model_dump(mode="json") for item in old.steps],
            "after": [item.model_dump(mode="json") for item in new.steps],
        },
        "constraints": {"before": old.constraints, "after": new.constraints},
    }
