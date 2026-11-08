"""运行时技能工具：模型在任务中自主发现、加载技能并读取资源。

身份不通过工具参数传递，而是从 contextvar 读取当前请求的用户技能运行时。
这些工具全部是只读的，属于 low 风险，可在 Agent 循环里被模型直接调用。
"""

from __future__ import annotations

import json
from contextvars import ContextVar

from pydantic import BaseModel, ConfigDict, Field

from app.skills.errors import SkillError
from app.skills.models import SkillActor, SkillQuery, SkillStatus
from app.skills.resources import read_resource

CURRENT_SKILL_RUNTIME: ContextVar[object | None] = ContextVar("skill_runtime", default=None)
CURRENT_SKILL_PRINCIPAL: ContextVar[str | None] = ContextVar("skill_principal", default=None)

# 注入系统提示词的「初始指令」：让模型知道该在任务开始前主动发现并加载技能。
SKILL_USAGE_INSTRUCTION = (
    "【技能使用规范】\n"
    "开始一项任务前，先调用 discover_skills 查看已安装且与该任务匹配的技能。\n"
    "若发现匹配的技能，调用 load_skill 加载其完整说明，并严格遵循其中的步骤、约束与方法论。\n"
    "当技能说明引用了特定资源文件时，再用 read_skill_resource 按需读取。\n"
    "若没有任何技能与当前任务相关，直接正常完成即可，不要强行使用技能。"
)


def _context() -> tuple[object, SkillActor] | None:
    runtime = CURRENT_SKILL_RUNTIME.get()
    if runtime is None:
        return None
    principal = CURRENT_SKILL_PRINCIPAL.get() or None
    return runtime, SkillActor(user_id=principal)


def _error(name: str, code: str, message: str) -> str:
    return json.dumps(
        {"source": "server_tool", "name": name, "error": {"code": code, "message": message}},
        ensure_ascii=False,
    )


class DiscoverSkillsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(default="", max_length=200)


def discover_skills(args: DiscoverSkillsArgs) -> str:
    ctx = _context()
    if ctx is None:
        return _error("discover_skills", "SKILL_RUNTIME_UNAVAILABLE", "技能运行时未开启")
    runtime, actor = ctx
    registry = runtime.orchestrator.registry  # type: ignore[attr-defined]
    metas = registry.query(SkillQuery(actor=actor, status=SkillStatus.ENABLED))
    keyword = args.query.strip().lower()
    if keyword:
        metas = [item for item in metas if _matches(item.name, item.description, item.category, item.tags, keyword)]
    return json.dumps(
        {
            "source": "local",
            "count": len(metas),
            "skills": [
                {
                    "skill_id": item.skill_id,
                    "name": item.name,
                    "description": item.description,
                    "category": item.category,
                    "tags": list(item.tags),
                }
                for item in metas
            ],
        },
        ensure_ascii=False,
    )


class LoadSkillArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str = Field(min_length=1, max_length=64)


def load_skill(args: LoadSkillArgs) -> str:
    ctx = _context()
    if ctx is None:
        return _error("load_skill", "SKILL_RUNTIME_UNAVAILABLE", "技能运行时未开启")
    runtime, actor = ctx
    orchestrator = runtime.orchestrator  # type: ignore[attr-defined]
    registry = orchestrator.registry
    loader = orchestrator.loader
    try:
        skill = registry.get(args.skill_id, actor=actor)
        loaded = loader.load(args.skill_id, skill.metadata.version, actor=actor)
    except SkillError as exc:
        return _error("load_skill", exc.code, exc.message)
    return json.dumps(
        {
            "source": "local",
            "skill_id": loaded.skill_id,
            "name": loaded.name,
            "version": loaded.version,
            "instructions": loaded.instruction_body,
        },
        ensure_ascii=False,
    )


class ReadSkillResourceArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str = Field(min_length=1, max_length=64)
    path: str = Field(min_length=1, max_length=180)


def read_skill_resource(args: ReadSkillResourceArgs) -> str:
    ctx = _context()
    if ctx is None:
        return _error("read_skill_resource", "SKILL_RUNTIME_UNAVAILABLE", "技能运行时未开启")
    runtime, actor = ctx
    orchestrator = runtime.orchestrator  # type: ignore[attr-defined]
    registry = orchestrator.registry
    try:
        skill = registry.get(args.skill_id, actor=actor)
        package = registry.package_dir(args.skill_id, skill.metadata.version, actor=actor)
        text, _digest, size = read_resource(package, args.path)
    except SkillError as exc:
        return _error("read_skill_resource", exc.code, exc.message)
    return json.dumps(
        {
            "source": "local",
            "path": args.path,
            "size_bytes": size,
            "content": text,
        },
        ensure_ascii=False,
    )


def _matches(
    name: str,
    description: str,
    category: str | None,
    tags: tuple[str, ...],
    keyword: str,
) -> bool:
    haystacks = [name.lower(), description.lower()]
    if category:
        haystacks.append(category.lower())
    haystacks.extend(tags)
    return any(keyword in item for item in haystacks)
