""".plan.md 导出产物：frontmatter 结构、正文渲染与文件名。"""

from __future__ import annotations

from datetime import UTC, datetime

from app.planning.contracts import (
    Assumption,
    AssumptionStatus,
    PlanDecision,
    PlanSnapshot,
    PlanStatus,
    PlanStep,
    PlanTodo,
    PlanTodoSet,
    TodoStatus,
)
from app.planning.plan_markdown import plan_filename, render_plan_markdown

NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)


def _plan(**overrides) -> PlanSnapshot:
    values = dict(
        conversation_id="conv-a",
        plan_id="plan_abc123456789",
        revision=3,
        status=PlanStatus.EXECUTING,
        goal="构建权限系统\n支持 RBAC",
        created_at=NOW,
        updated_at=NOW,
    )
    values.update(overrides)
    return PlanSnapshot(**values)


def test_filename_is_slug_plus_short_id() -> None:
    plan = _plan()
    filename = plan_filename(plan)
    assert filename.startswith("plan_")
    assert filename.endswith(".plan.md")
    # plan_id 后 8 位会进入文件名，保证同名目标不冲突。
    assert "23456789" in filename


def test_filename_uses_ascii_slug_for_english_goal() -> None:
    plan = _plan(goal="Build a permission system")
    filename = plan_filename(plan)
    assert filename.startswith("plan_build_a_permission_")
    assert filename.endswith("_23456789.plan.md")


def test_frontmatter_contains_name_overview_todos_and_is_project() -> None:
    plan = _plan(
        steps=[PlanStep(id="step_1", title="Define roles")],
    )
    markdown = render_plan_markdown(plan)

    assert markdown.startswith("---\n")
    assert "name:" in markdown
    assert "overview:" in markdown
    assert "isProject: false" in markdown
    assert "todos:" in markdown
    assert "- id: \"step_1\"" in markdown
    assert "content: \"Define roles\"" in markdown
    assert "status: pending" in markdown


def test_todos_derive_pending_from_steps_when_absent() -> None:
    plan = _plan(
        steps=[
            PlanStep(id="step_1", title="Define roles", depends_on=[]),
            PlanStep(id="step_2", title="Map grants", depends_on=["step_1"]),
        ],
    )
    markdown = render_plan_markdown(plan)

    assert "- id: \"step_1\"" in markdown
    assert "- id: \"step_2\"" in markdown
    assert markdown.count("status: pending") == 2


def test_todos_render_four_states_in_checklist() -> None:
    plan = _plan()
    todos = PlanTodoSet(
        plan_id=plan.plan_id,
        source_plan_revision=plan.revision,
        updated_at=NOW,
        todos=[
            PlanTodo(id="a", content="待办", status=TodoStatus.PENDING),
            PlanTodo(id="b", content="进行", status=TodoStatus.IN_PROGRESS),
            PlanTodo(id="c", content="完成", status=TodoStatus.COMPLETED),
            PlanTodo(id="d", content="取消", status=TodoStatus.CANCELLED),
        ],
    )
    markdown = render_plan_markdown(plan, todos)

    assert "- [ ] 待办" in markdown
    assert "- [~] 进行" in markdown
    assert "- [x] 完成" in markdown
    assert "- [-] 取消" in markdown


def test_goal_with_special_characters_does_not_break_yaml() -> None:
    plan = _plan(goal='目标含 "引号"、冒号: 与\n换行')
    markdown = render_plan_markdown(plan)

    # overview 用双引号标量 + \\n 转义，换行不应出现在裸 YAML 结构里。
    overview_line = next(line for line in markdown.splitlines() if line.startswith("overview:"))
    assert '\\n' in overview_line
    assert '\\"' in overview_line


def test_body_includes_decisions_assumptions_and_constraints() -> None:
    plan = _plan(
        decisions=[PlanDecision(key="model", value="rbac", source="user", confidence=1.0)],
        assumption_items=[
            Assumption(id="a1", text="用户数小于 1000", status=AssumptionStatus.CONFIRMED)
        ],
        constraints=["必须支持 SSO"],
        steps=[PlanStep(id="step_1", title="Define roles")],
    )
    markdown = render_plan_markdown(plan)

    assert "## 目标" in markdown
    assert "## 决策" in markdown
    assert "**model**：rbac（用户确认）" in markdown
    assert "## 假设" in markdown
    assert "[confirmed] 用户数小于 1000" in markdown
    assert "## 约束" in markdown
    assert "- 必须支持 SSO" in markdown
    assert "## 步骤" in markdown


def test_body_marks_plan_revision_and_status() -> None:
    plan = _plan()
    markdown = render_plan_markdown(plan)

    assert "> 计划 v3 · 状态 executing" in markdown
