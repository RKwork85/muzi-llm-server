"""服务端工具注册表。第一阶段只登记 low 风险工具。"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.tools.calculator import CalculatorArgs, calculator_handler
from app.tools.clock import EmptyArgs, current_time_handler
from app.tools.deliver_file import DeliverArgs, deliver_file
from app.tools.docx_tool import WriteDocxArgs, write_docx
from app.tools.file_changes import DeleteArgs, WriteArgs, delete_file, write_text_file
from app.tools.file_tools import ListArgs, ReadArgs, SearchArgs as FileSearchArgs, list_directory, read_text_file, search_text
from app.tools.code_runner import PythonArgs, run_python
from app.tools.sandbox_command import CommandArgs, run_command
from app.tools.weather import WeatherArgs, weather_query
from app.tools.local_tools import DateDiffArgs, FormatJsonArgs, TextStatsArgs, date_diff, format_json, text_stats
from app.tools.mermaid import (
    FlowchartArgs,
    SequenceArgs,
    ValidateArgs,
    create_flowchart,
    create_sequence_diagram,
    validate_mermaid,
)
from app.tools.prompts import tool_prompt
from app.tools.schemas import parameters_schema
from app.tools.data_tools import register_data_tools
from app.tools.web_tools import FetchArgs, SearchArgs, web_fetch, web_search
from app.skills.tools import (
    DiscoverSkillsArgs,
    LoadSkillArgs,
    ReadSkillResourceArgs,
    discover_skills,
    load_skill,
    read_skill_resource,
)

_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
KNOWN_TOOLSETS = frozenset(
    {"basic", "utility", "diagram", "research", "weather", "workspace", "code", "data", "delivery", "mcp", "skills"}
)
KNOWN_ACCESS = frozenset(
    {
        "read_local",
        "read_network",
        "read_files",
        "write_data",
        "write_remote",
        "mutate_remote",
        "write_workspace",
        "delete_workspace",
        "sandbox_exec",
    }
)
REJECTED_ACCESS = frozenset({"write_local", "write_external"})
MUTABLE_ACCESS = frozenset({"write_workspace", "delete_workspace", "sandbox_exec", "mutate_remote"})


@dataclass
class ToolSpec:
    name: str
    description: str = ""
    args_model: type[BaseModel] | None = None
    handler: Callable[..., Any] | None = None
    risk_level: str = "low"
    access: str = "read_local"
    requires_network: bool = False
    timeout_s: float | None = None
    max_result_chars: int | None = None
    max_calls_per_run: int | None = None
    idempotent: bool = True
    toolset: str = "basic"
    parameters: dict[str, Any] | None = None

    def openai_schema(self) -> dict[str, Any]:
        if self.parameters is not None:
            parameters = self.parameters
        else:
            parameters = parameters_schema(self.args_model)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.args_model is None or spec.handler is None:
            raise ValueError("工具缺少参数模型或处理函数")
        if not spec.description.strip():
            spec.description = tool_prompt(spec.name)
        if spec.risk_level != "low":
            raise ValueError("第一阶段仅允许注册 low 风险工具")
        if spec.access in REJECTED_ACCESS:
            raise ValueError("本阶段拒绝写入类工具")
        if spec.access not in KNOWN_ACCESS:
            raise ValueError("工具访问级别不合法")
        if spec.toolset not in KNOWN_TOOLSETS:
            raise ValueError("工具集不合法")
        if spec.idempotent == (spec.access in MUTABLE_ACCESS):
            raise ValueError("写入、删除和命令工具必须标记为非幂等，其余工具必须是只读")
        if spec.requires_network != (spec.access in {"read_network", "write_remote", "mutate_remote"}):
            raise ValueError("联网标记与访问级别不一致")
        if spec.requires_network and not inspect.iscoroutinefunction(spec.handler):
            raise ValueError("联网工具必须是 async")
        if spec.timeout_s is not None and not 0 < spec.timeout_s <= 30:
            raise ValueError("工具超时超出范围")
        if spec.max_result_chars is not None and not 96 <= spec.max_result_chars <= 20000:
            raise ValueError("工具结果长度超出范围")
        if spec.max_calls_per_run is not None and not 1 <= spec.max_calls_per_run <= 32:
            raise ValueError("工具调用次数超出范围")
        if not _TOOL_NAME.fullmatch(spec.name):
            raise ValueError("工具名称不合法")
        if spec.name in self._tools:
            raise ValueError("工具名称重复")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def all_tools(self) -> list[ToolSpec]:
        return list(self._tools.values())


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="calculator",
            args_model=CalculatorArgs,
            handler=calculator_handler,
            risk_level="low",
        )
    )
    registry.register(
        ToolSpec(
            name="get_current_time",
            args_model=EmptyArgs,
            handler=current_time_handler,
            risk_level="low",
        )
    )
    return registry


def build_agent_registry(search_calls: int = 5, fetch_calls: int = 5) -> ToolRegistry:
    """Agent 用的注册表。Phase 1 仍使用 build_default_registry，避免默认暴露新工具。"""
    registry = build_default_registry()
    registry.register(
        ToolSpec(
            name="format_json",
            args_model=FormatJsonArgs,
            handler=format_json,
            toolset="utility",
            max_calls_per_run=5,
        )
    )
    registry.register(
        ToolSpec(
            name="text_stats",
            args_model=TextStatsArgs,
            handler=text_stats,
            toolset="utility",
            max_calls_per_run=5,
        )
    )
    registry.register(
        ToolSpec(
            name="date_diff",
            args_model=DateDiffArgs,
            handler=date_diff,
            toolset="utility",
            max_calls_per_run=5,
        )
    )
    registry.register(
        ToolSpec(
            name="create_flowchart",
            args_model=FlowchartArgs,
            handler=create_flowchart,
            toolset="diagram",
            max_calls_per_run=5,
        )
    )
    registry.register(
        ToolSpec(
            name="create_sequence_diagram",
            args_model=SequenceArgs,
            handler=create_sequence_diagram,
            toolset="diagram",
            max_calls_per_run=5,
        )
    )
    registry.register(
        ToolSpec(
            name="validate_mermaid",
            args_model=ValidateArgs,
            handler=validate_mermaid,
            toolset="diagram",
            max_calls_per_run=5,
        )
    )
    registry.register(
        ToolSpec(
            name="web_search",
            args_model=SearchArgs,
            handler=web_search,
            access="read_network",
            requires_network=True,
            toolset="research",
            max_calls_per_run=search_calls,
            timeout_s=20,
        )
    )
    registry.register(
        ToolSpec(
            name="web_fetch",
            args_model=FetchArgs,
            handler=web_fetch,
            access="read_network",
            requires_network=True,
            toolset="research",
            max_calls_per_run=fetch_calls,
            timeout_s=20,
        )
    )
    registry.register(
        ToolSpec(
            name="list_directory",
            args_model=ListArgs,
            handler=list_directory,
            access="read_files",
            toolset="workspace",
            max_calls_per_run=5,
            timeout_s=10,
        )
    )
    registry.register(
        ToolSpec(
            name="read_text_file",
            args_model=ReadArgs,
            handler=read_text_file,
            access="read_files",
            toolset="workspace",
            max_calls_per_run=5,
            timeout_s=10,
        )
    )
    registry.register(
        ToolSpec(
            name="search_text",
            args_model=FileSearchArgs,
            handler=search_text,
            access="read_files",
            toolset="workspace",
            max_calls_per_run=5,
            timeout_s=10,
        )
    )
    registry.register(
        ToolSpec(
            name="weather_query",
            args_model=WeatherArgs,
            handler=weather_query,
            access="read_network",
            requires_network=True,
            toolset="weather",
            max_calls_per_run=5,
            timeout_s=15,
        )
    )
    registry.register(
        ToolSpec(
            name="write_text_file",
            args_model=WriteArgs,
            handler=write_text_file,
            access="write_workspace",
            idempotent=False,
            toolset="workspace",
            max_calls_per_run=5,
            timeout_s=10,
        )
    )
    registry.register(
        ToolSpec(
            name="write_docx",
            args_model=WriteDocxArgs,
            handler=write_docx,
            access="write_workspace",
            idempotent=False,
            toolset="workspace",
            max_calls_per_run=5,
            timeout_s=10,
        )
    )
    registry.register(
        ToolSpec(
            name="delete_file",
            args_model=DeleteArgs,
            handler=delete_file,
            access="delete_workspace",
            idempotent=False,
            toolset="workspace",
            max_calls_per_run=5,
            timeout_s=10,
        )
    )
    registry.register(
        ToolSpec(
            name="deliver_file",
            args_model=DeliverArgs,
            handler=deliver_file,
            access="write_remote",
            requires_network=True,
            toolset="delivery",
            max_calls_per_run=5,
            timeout_s=25,
        )
    )
    registry.register(
        ToolSpec(
            name="run_command",
            args_model=CommandArgs,
            handler=run_command,
            access="sandbox_exec",
            idempotent=False,
            toolset="workspace",
            max_calls_per_run=3,
            timeout_s=20,
        )
    )
    registry.register(
        ToolSpec(
            name="run_python",
            args_model=PythonArgs,
            handler=run_python,
            access="sandbox_exec",
            idempotent=False,
            toolset="code",
            max_calls_per_run=3,
            timeout_s=20,
        )
    )
    registry.register(
        ToolSpec(
            name="discover_skills",
            description=(
                "列出当前已安装且可用的技能及其简要说明。开始一项任务前先调用它。"
                "建议第一次不带 query 调用以浏览全部技能，再自行判断哪个技能与当前任务最相关；"
                "query 仅用于技能较多时按关键词过滤（匹配技能名、描述、分类或标签）。"
            ),
            args_model=DiscoverSkillsArgs,
            handler=discover_skills,
            risk_level="low",
            toolset="skills",
            max_calls_per_run=8,
        )
    )
    registry.register(
        ToolSpec(
            name="load_skill",
            description=(
                "加载一个技能的完整操作说明（SKILL.md 正文）。先用 discover_skills 找到 skill_id，"
                "再调用本工具把它的详细指令读进上下文并严格按其中的步骤执行。"
            ),
            args_model=LoadSkillArgs,
            handler=load_skill,
            risk_level="low",
            toolset="skills",
            max_calls_per_run=16,
        )
    )
    registry.register(
        ToolSpec(
            name="read_skill_resource",
            description=(
                "读取某个技能包内的引用资源（references/assets/templates 目录下的 .md/.txt/.json 文件）。"
                "当技能说明提到需要参考某个资源时使用，path 为相对该技能目录的路径。"
            ),
            args_model=ReadSkillResourceArgs,
            handler=read_skill_resource,
            risk_level="low",
            toolset="skills",
            max_calls_per_run=16,
        )
    )
    register_data_tools(registry)
    return registry
