"""把计划快照渲染成 Cursor 风格的 `.plan.md` 产物。

只做**导出/存档**：todos 仍以 Postgres 为唯一真相，文件只是渲染快照，
不写回仓库、不参与执行期进度合并（避免 Cursor「todos 混进文件导致 Build 失效」的坑）。
"""

from __future__ import annotations

import re

from app.planning.contracts import (
    PlanSnapshot,
    PlanTodo,
    PlanTodoSet,
    TodoStatus,
)

_MAX_NAME_LEN = 80
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")

_STATUS_LABEL = {
    "user": "用户确认",
    "assumed": "假设",
    "system": "系统",
}

_TODO_CHECK = {
    TodoStatus.PENDING: "- [ ]",
    TodoStatus.IN_PROGRESS: "- [~]",
    TodoStatus.COMPLETED: "- [x]",
    TodoStatus.CANCELLED: "- [-]",
}

_TODO_LABEL = {
    TodoStatus.PENDING: "待执行",
    TodoStatus.IN_PROGRESS: "进行中",
    TodoStatus.COMPLETED: "已完成",
    TodoStatus.CANCELLED: "已取消",
}


def _yaml_str(value: str) -> str:
    """把任意字符串安全编码成 YAML 双引号标量（单行，换行转义为 \\n）。"""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _derive_name(goal: str) -> str:
    """从计划目标派生一个简短名称，作为 frontmatter 的 name。"""
    first_line = next((line.strip() for line in goal.splitlines() if line.strip()), "").strip()
    name = first_line or goal.strip() or "未命名计划"
    return name[:_MAX_NAME_LEN]


def _slugify(name: str, limit: int = 24) -> str:
    slug = _SLUG_STRIP.sub("_", name.lower()).strip("_")
    return (slug or "plan")[:limit]


def plan_filename(plan: PlanSnapshot) -> str:
    """导出文件名：plan_<slug>_<plan_id 后 8 位>.plan.md。"""
    short_id = plan.plan_id.split("_", 1)[-1][-8:] or "00000000"
    return f"plan_{_slugify(_derive_name(plan.goal))}_{short_id}.plan.md"


def _todos_or_derived(plan: PlanSnapshot, todos: PlanTodoSet | None) -> list[PlanTodo]:
    """执行期清单优先；尚未执行时从计划步骤派生全 pending 清单。"""
    if todos is not None:
        return list(todos.todos)
    return [
        PlanTodo(id=step.id, content=step.title, depends_on=list(step.depends_on))
        for step in plan.steps
    ]


def _frontmatter(plan: PlanSnapshot, todos: list[PlanTodo]) -> str:
    name = _derive_name(plan.goal)
    lines = [
        "---",
        f"name: {_yaml_str(name)}",
        f"overview: {_yaml_str(plan.goal)}",
        "todos:",
    ]
    if todos:
        for todo in todos:
            lines.append(f"  - id: {_yaml_str(todo.id)}")
            lines.append(f"    content: {_yaml_str(todo.content)}")
            lines.append(f"    status: {todo.status.value}")
    else:
        lines.append("  []")
    lines.append("isProject: false")
    lines.append("---")
    return "\n".join(lines)


def _step_title_index(plan: PlanSnapshot) -> dict[str, str]:
    return {step.id: step.title for step in plan.steps}


def _render_dependencies(deps: list[str], index: dict[str, str]) -> str:
    if not deps:
        return ""
    labels = [index.get(item, item) for item in deps]
    return f"（依赖：{', '.join(labels)}）"


def _body(plan: PlanSnapshot, todos: list[PlanTodo]) -> str:
    index = _step_title_index(plan)
    name = _derive_name(plan.goal)
    chunks: list[str] = [
        f"# {name}",
        "",
        f"> 计划 v{plan.revision} · 状态 {plan.status.value} · 更新时间 {plan.updated_at.isoformat()}",
        "",
        "## 目标",
        "",
        plan.goal,
    ]

    if plan.constraints:
        chunks += ["", "## 约束", ""]
        chunks += [f"- {item}" for item in plan.constraints]

    if plan.decisions:
        chunks += ["", "## 决策", ""]
        for decision in plan.decisions:
            source = _STATUS_LABEL.get(decision.source, decision.source)
            chunks.append(f"- **{decision.key}**：{decision.value}（{source}）")

    assumptions = [
        item for item in plan.assumption_items
        if getattr(item, "text", "").strip()
    ]
    if assumptions:
        chunks += ["", "## 假设", ""]
        for item in assumptions:
            status = getattr(item, "status", None)
            mark = f"[{status.value}] " if status is not None else ""
            chunks.append(f"- {mark}{item.text}")

    if plan.steps:
        chunks += ["", "## 步骤", ""]
        for position, step in enumerate(plan.steps, start=1):
            deps = _render_dependencies(step.depends_on, index)
            chunks.append(f"{position}. **{step.title}**{deps}".rstrip())
            if step.description:
                chunks.append(f"   {step.description}")

    if todos:
        chunks += ["", "## 执行进度", ""]
        for todo in todos:
            mark = _TODO_CHECK.get(todo.status, "- [ ]")
            label = _TODO_LABEL.get(todo.status, "")
            chunks.append(f"{mark} {todo.content}  `{label}`" if label else f"{mark} {todo.content}")

    chunks.append("")
    return "\n".join(chunks)


def render_plan_markdown(plan: PlanSnapshot, todos: PlanTodoSet | None = None) -> str:
    """渲染完整 `.plan.md` 内容：YAML frontmatter + 正文。"""
    items = _todos_or_derived(plan, todos)
    return f"{_frontmatter(plan, items)}\n\n{_body(plan, items)}\n"
