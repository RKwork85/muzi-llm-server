"""注册一个带 runtime 的可执行演示技能，用于验证 AgentRuntime 显式激活链路。

技能 ``echo-demo``：读取 args.json 里的 message 并回显，同时打印固定标记 ``DEMO_SKILL_OK``。
重复执行时若同名技能已存在则跳过注册（幂等）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.skills.frontmatter import parse_skill_markdown
from app.skills.models import (
    SkillAction,
    SkillActor,
    SkillQuery,
    SkillScope,
    SkillSource,
    SkillSourceKind,
    SkillStatus,
)
from app.skills.registry import FileSkillRegistry

TARGET = ROOT / "skills_data"

SKILL_MD = """---
name: echo-demo
description: 一个用于验证 AgentRuntime 显式激活与沙盒执行的最小可执行技能。当用户要求「回显一段文本」或「运行演示脚本」时使用。
version: 1.0.0
runtime:
  language: python
  entrypoint: main.py
  timeout: 15
---
运行 main.py 脚本：读取 args.json 里的 message 字段原样回显，并额外打印一行固定标记 DEMO_SKILL_OK。
"""

SCRIPT = """import json

args = json.load(open("args.json", encoding="utf-8"))
msg = args.get("message", "hello")
print("echo:", msg)
print("DEMO_SKILL_OK")
"""


def main() -> None:
    registry = FileSkillRegistry(TARGET, allow_system=True)
    actor = SkillActor()
    existing = [
        item
        for item in registry.query(SkillQuery(actor=actor, scope=SkillScope.SYSTEM))
        if item.name == "echo-demo"
    ]
    if existing:
        skill_id = existing[0].skill_id
        skill = registry.get(skill_id, actor=actor)
        if skill.metadata.status == SkillStatus.REGISTERED:
            registry.apply(skill_id, SkillAction.INSTALL, actor=actor)
            skill = registry.get(skill_id, actor=actor)
        if skill.metadata.status == SkillStatus.INSTALLED:
            registry.apply(skill_id, SkillAction.ENABLE, actor=actor)
        print(f"exists {skill_id} ({skill.metadata.status.value})")
        return

    manifest, body = parse_skill_markdown(SKILL_MD)
    skill = registry.register(
        manifest,
        body,
        actor=actor,
        source=SkillSource(kind=SkillSourceKind.LOCAL_DIRECTORY, label="demo"),
        scope=SkillScope.SYSTEM,
    )
    registry.apply(skill.metadata.skill_id, SkillAction.INSTALL, actor=actor)
    registry.apply(skill.metadata.skill_id, SkillAction.ENABLE, actor=actor)
    package = registry.package_dir(skill.metadata.skill_id, "1.0.0", actor=actor)
    (package / "main.py").write_text(SCRIPT, encoding="utf-8")
    print(f"registered {skill.metadata.skill_id}")


if __name__ == "__main__":
    main()
