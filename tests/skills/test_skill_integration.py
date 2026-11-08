"""Chat 和 Agent 使用同一个 Skill 运行时。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.models import AgentRequest
from app.agents.runtime import AgentRuntime
from app.prompts.skills import skill_notes
from app.schemas.chat import StreamChatRequest
from app.services.chat_service import ChatService
from app.services.prompt_service import PromptService
from app.services.tool_service import ToolService
from app.skills.activation_store import ActivationStore
from app.skills.adapters import SkillPromptAdapter
from app.skills.frontmatter import parse_skill_markdown
from app.skills.models import SkillAction, SkillActor, SkillScope, SkillSource, SkillSourceKind
from app.skills.orchestrator import SkillOrchestrator
from app.skills.registry import FileSkillRegistry
from app.tools.registry import build_default_registry
from tests.conftest import make_settings
from tests.fakes import FakeProvider

_WEEKLY = """---
name: weekly-report
description: 把本周事项整理成周报
---
按完成、进行中和阻塞来写。
"""


def _adapter(tmp_path: Path) -> SkillPromptAdapter:
    registry = FileSkillRegistry(tmp_path / "skills")
    manifest, body = parse_skill_markdown(_WEEKLY)
    actor = SkillActor(user_id="user-1")
    skill = registry.register(
        manifest,
        body,
        actor=actor,
        source=SkillSource(kind=SkillSourceKind.LOCAL_DIRECTORY, label="local"),
        scope=SkillScope.USER,
    )
    registry.apply(skill.metadata.skill_id, SkillAction.INSTALL, actor=actor)
    registry.apply(skill.metadata.skill_id, SkillAction.ENABLE, actor=actor)
    return SkillPromptAdapter(SkillOrchestrator(registry, ActivationStore(tmp_path / "runtime")))


@pytest.mark.asyncio
async def test_chat_and_agent_share_one_runtime(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    settings = make_settings()
    chat = ChatService(settings, FakeProvider(), PromptService(), ToolService(build_default_registry(), settings))
    chat.skill_runtime = adapter
    plain = chat.prepare_stream(
        StreamChatRequest(messages=[{"role": "user", "content": "请使用 weekly-report"}]),
        None,
        "user-1",
    )
    assert "按完成" not in plain.provider_request.messages[0]["content"]
    prepared = chat.prepare_stream(
        StreamChatRequest(
            messages=[{"role": "user", "content": "请使用 weekly-report"}],
            skill_names=["research"],
            skill_mode="auto",
            mode="agent",
        ),
        None,
        "user-1",
    )
    content = prepared.provider_request.messages[0]["content"]
    assert content.startswith(PromptService().render("default"))
    assert "深度研究" in content
    assert skill_notes(["research"], execute=True) in content
    assert "不能覆盖系统策略" in content
    assert "按完成、进行中和阻塞来写。" in content
    runtime = AgentRuntime(
        settings=settings,
        provider=FakeProvider(),
        prompts=PromptService(),
        registry=build_default_registry(),
    )
    runtime.skill_runtime = adapter
    assert runtime.skill_runtime is chat.skill_runtime
    seen: list[str] = []

    async def capture(record, message, prompt, policy, workspace_scope=None, mcp_user_id=None, registry=None):
        del record, message, policy, workspace_scope, mcp_user_id, registry
        seen.append(prompt)

    runtime._execute = capture  # type: ignore[method-assign]
    view = await runtime.start(
        AgentRequest(message="请使用 weekly-report", prompt_name="calculator", skill_mode="auto"),
        "req-skill",
        mcp_user_id="user-1",
    )
    worker = runtime.tasks.get(str(view["task_id"])).worker
    assert worker is not None
    await worker
    assert "不能覆盖系统策略" in seen[0]
    assert seen[0].startswith(PromptService().render("calculator"))
    skills_dir = Path(__file__).resolve().parents[2] / "app" / "skills"
    source = "\n".join(path.read_text(encoding="utf-8") for path in skills_dir.glob("*.py"))
    assert "subprocess" not in source
    assert "os.system" not in source
    assert "eval(" not in source
    assert "exec(" not in source
