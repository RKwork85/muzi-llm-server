"""执行期清单工具：把计划步骤的进度写回服务端。

只对「由计划交接而来的 Agent 运行」有意义：`plan_id` 只能来自服务端写入的
执行上下文，模型无法通过参数指定计划，因此不存在越权修改他人进度的问题。
"""

from __future__ import annotations

import json
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TodoStatusLiteral = Literal["pending", "in_progress", "completed", "cancelled"]


class TodoItemArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=2000)
    status: TodoStatusLiteral = "pending"
    depends_on: list[str] = Field(default_factory=list, max_length=50)


class UpdateTodosArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    todos: list[TodoItemArgs] = Field(default_factory=list, max_length=200)
    merge: bool = Field(
        default=True,
        description="true=按 id 增量更新（未提交的项保持原状）；false=整体替换，缺失项转为 cancelled。",
    )


async def update_todos(args: UpdateTodosArgs) -> str:
    # 延迟导入：工具注册表在模块层导入本文件，execution_guard 又经由 tools 包
    # 间接依赖注册表，因此所有跨包引用都必须放在函数内部。
    from app.authorization.application.execution_guard import CURRENT_EXECUTION
    from app.models.tables import User
    from app.planning.contracts import PlanTodo, TodoUpdateRequest
    from app.planning.runtime import PlanningConflict, PlanningNotFound, PlanningPolicyError
    from app.planning.sql_store import SqlPlanningStore

    context = CURRENT_EXECUTION.get()
    if context is None or not context.user_id:
        return _error("PLAN_CONTEXT_REQUIRED", "当前运行缺少可信身份")
    if not context.plan_id:
        return _error("PLAN_CONTEXT_REQUIRED", "当前运行未关联计划，无法回写清单")
    try:
        user_id = uuid.UUID(str(context.user_id))
    except ValueError:
        return _error("PLAN_CONTEXT_REQUIRED", "当前运行身份不可解析")

    try:
        request = TodoUpdateRequest(
            todos=[
                PlanTodo(
                    id=item.id,
                    content=item.content,
                    status=item.status,
                    depends_on=list(item.depends_on),
                )
                for item in args.todos
            ],
            merge=args.merge,
        )
    except ValueError:
        return _error("INVALID_TODO", "清单结构不合法（id 重复或字段超限）")

    try:
        from app.core.database.session import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            user = await session.get(User, user_id)
            if user is None:
                return _error("PLAN_CONTEXT_REQUIRED", "用户不存在")
            checklist = await SqlPlanningStore(session).update_todos(context.plan_id, user, request)
            await session.commit()
    except PlanningNotFound:
        return _error("PLAN_NOT_FOUND", "计划不存在")
    except PlanningConflict as exc:
        return _error("PLAN_CONFLICT", str(exc))
    except PlanningPolicyError as exc:
        return _error("PLAN_POLICY_REJECTED", str(exc))
    except Exception:  # noqa: BLE001 - 数据层故障以结构化错误回给模型
        return _error("TODO_UPDATE_FAILED", "清单写入失败，请稍后重试")

    return json.dumps(
        {
            "ok": True,
            "plan_id": checklist.plan_id,
            "source_plan_revision": checklist.source_plan_revision,
            "todos": [todo.model_dump(mode="json") for todo in checklist.todos],
            "summary": "清单已更新。继续执行下一步前无需重复提交相同的状态。",
        },
        ensure_ascii=False,
    )


def _error(code: str, message: str) -> str:
    return json.dumps({"ok": False, "error": {"code": code, "message": message}}, ensure_ascii=False)


__all__ = ["TodoItemArgs", "UpdateTodosArgs", "update_todos"]
