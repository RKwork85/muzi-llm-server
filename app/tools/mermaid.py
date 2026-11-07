"""结构化数据生成 Mermaid 源码。不渲染，也不保证客户端一定能画出来。"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_KEY = re.compile(r"^[^\s\"'`|<>]{1,32}$")
_FIRST = re.compile(
    r"^(flowchart|graph|sequenceDiagram|stateDiagram-v2|classDiagram|erDiagram)\b"
)
_BANNED = (
    (re.compile(r"%%\{"), "config"),
    (re.compile(r"(?i)\bclick\b"), "interactive"),
    (re.compile(r"(?i)\bhref\b"), "interactive"),
    (re.compile(r"(?i)\bcallback\b"), "interactive"),
    (re.compile(r"(?i)\blinks?\b"), "interactive"),
    (re.compile(r"(?i)javascript:"), "html"),
    (re.compile(r"(?i)data:"), "html"),
    (re.compile(r"(?i)<script"), "html"),
    (re.compile(r"(?i)<iframe"), "html"),
    (re.compile(r"(?i)<img"), "html"),
    (re.compile(r"(?i)<a "), "html"),
    (re.compile(r"(?i)\bon\w+\s*="), "html"),
)
_RESERVED_ID = re.compile(r"(?i)\bend\b")

_FLOW_SHAPES = {
    "rect": '["{label}"]',
    "round": '("{label}")',
    "stadium": '(["{label}"])',
    "diamond": '{{"{label}"}}',
    "circle": '(("{label}"))',
}
_FLOW_ARROWS = {"solid": "-->", "dotted": "-.->", "thick": "==>"}
_SEQ_ARROWS = {"sync": "->>", "async": "-)", "reply": "-->>"}


class FlowNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=_KEY.pattern)
    label: str = Field(min_length=1, max_length=80)
    shape: Literal["rect", "round", "stadium", "diamond", "circle"] = "rect"


class FlowEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    label: str | None = Field(default=None, max_length=60)
    style: Literal["solid", "dotted", "thick"] = "solid"


class FlowchartArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: Literal["TB", "LR", "BT", "RL"] = "TB"
    title: str | None = Field(default=None, max_length=80)
    nodes: list[FlowNode] = Field(min_length=1, max_length=60)
    edges: list[FlowEdge] = Field(default_factory=list, max_length=120)

    @model_validator(mode="after")
    def _keys(self) -> FlowchartArgs:
        keys = [node.key for node in self.nodes]
        if len(keys) != len(set(keys)):
            raise ValueError("节点 key 重复")
        known = set(keys)
        for edge in self.edges:
            if edge.source not in known or edge.target not in known:
                raise ValueError("边引用了不存在的节点")
        return self


class Participant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=_KEY.pattern)
    label: str = Field(min_length=1, max_length=60)


class SequenceMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    text: str = Field(min_length=1, max_length=120)
    kind: Literal["sync", "async", "reply"] = "sync"


class SequenceArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=80)
    participants: list[Participant] = Field(min_length=1, max_length=20)
    messages: list[SequenceMessage] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _keys(self) -> SequenceArgs:
        keys = [item.key for item in self.participants]
        if len(keys) != len(set(keys)):
            raise ValueError("参与者 key 重复")
        known = set(keys)
        for message in self.messages:
            if message.source not in known or message.target not in known:
                raise ValueError("消息引用了不存在的参与者")
        return self


class ValidateArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=20000)


def escape_mermaid(text: str) -> str:
    """按官方实体写法转义。# 用十进制 #35;，分号用官方写明的 #59;。"""
    pieces: list[str] = []
    for char in text:
        if char == "\n" or char == "\r" or char == "\t":
            pieces.append(" ")
        elif ord(char) < 32:
            continue
        elif char == '"':
            pieces.append("#quot;")
        elif char == "<":
            pieces.append("#lt;")
        elif char == ">":
            pieces.append("#gt;")
        elif char == "&":
            pieces.append("#amp;")
        elif char == "#":
            pieces.append("#35;")
        elif char == ";":
            pieces.append("#59;")
        elif char == "|":
            pieces.append("#124;")
        elif char == "`":
            pieces.append("#96;")
        else:
            pieces.append(char)
    return "".join(pieces)


def render_flowchart(args: FlowchartArgs) -> str:
    ids = {node.key: f"n{index}" for index, node in enumerate(args.nodes, start=1)}
    lines = [f"flowchart {args.direction}"]
    for node in args.nodes:
        label = escape_mermaid(node.label)
        lines.append(f"  {ids[node.key]}{_FLOW_SHAPES[node.shape].format(label=label)}")
    for edge in args.edges:
        arrow = _FLOW_ARROWS[edge.style]
        left = ids[edge.source]
        right = ids[edge.target]
        if edge.label:
            lines.append(f"  {left} {arrow}|\"{escape_mermaid(edge.label)}\"| {right}")
        else:
            lines.append(f"  {left} {arrow} {right}")
    return "\n".join(lines) + "\n"


def render_sequence(args: SequenceArgs) -> str:
    ids = {item.key: f"p{index}" for index, item in enumerate(args.participants, start=1)}
    lines = ["sequenceDiagram"]
    for item in args.participants:
        lines.append(f"  participant {ids[item.key]} as {escape_mermaid(item.label)}")
    for message in args.messages:
        arrow = _SEQ_ARROWS[message.kind]
        lines.append(
            f"  {ids[message.source]}{arrow}{ids[message.target]}: {escape_mermaid(message.text)}"
        )
    return "\n".join(lines) + "\n"


def flowchart_warnings(args: FlowchartArgs) -> list[str]:
    linked: set[str] = set()
    for edge in args.edges:
        linked.add(edge.source)
        linked.add(edge.target)
    if any(node.key not in linked for node in args.nodes):
        return ["存在没有连线的节点"]
    return []


async def create_flowchart(args: FlowchartArgs) -> str:
    source = render_flowchart(args)
    return _diagram_payload("flowchart", source, args.title, len(args.nodes), len(args.edges), flowchart_warnings(args), "create_flowchart")


async def create_sequence_diagram(args: SequenceArgs) -> str:
    source = render_sequence(args)
    return _diagram_payload(
        "sequenceDiagram",
        source,
        args.title,
        len(args.participants),
        len(args.messages),
        [],
        "create_sequence_diagram",
    )


async def validate_mermaid(args: ValidateArgs) -> str:
    normalized, diagram_type, issues, warnings = inspect_mermaid(args.source)
    body: dict[str, object] = {
        "valid": not issues,
        "diagram_type": diagram_type,
        "normalized": normalized if not issues else "",
        "line_count": 0 if issues else len(normalized.splitlines()),
        "issues": issues,
        "warnings": warnings,
        "checked": "lexical_safety_and_structure",
        "guarantees_render": False,
    }
    if not issues:
        body["artifacts"] = [
            {
                "type": "mermaid",
                "title": None,
                "content": normalized,
                "content_type": "text/vnd.mermaid",
                "source_tool": "validate_mermaid",
            }
        ]
    return json.dumps(body, ensure_ascii=False)


def inspect_mermaid(source: str) -> tuple[str, str | None, list[str], list[str]]:
    issues: list[str] = []
    if "\x00" in source or any(ord(char) < 32 and char not in "\n\r\t" for char in source):
        issues.append("control_char")
    if len(source) > 20000:
        issues.append("too_long")
    normalized = _normalize(source)
    lines = normalized.splitlines()
    if len(lines) > 400:
        issues.append("line_limit")
    if normalized.startswith("---\n") or normalized.startswith("---"):
        issues.append("config")
    for pattern, category in _BANNED:
        if pattern.search(normalized) and category not in issues:
            issues.append(category)
    statement = ""
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("%%"):
            continue
        statement = stripped
        break
    match = _FIRST.match(statement)
    diagram_type = match.group(1) if match else None
    if diagram_type is None and "unknown_type" not in issues:
        issues.append("unknown_type")
    warnings: list[str] = []
    if diagram_type in {"flowchart", "graph"} and _RESERVED_ID.search(normalized):
        warnings.append("flowchart 中使用了节点 ID 'end'，可能导致渲染失败")
    if normalized.count('"') % 2 == 1:
        warnings.append("引号可能未闭合")
    return normalized, diagram_type, issues, warnings


def _normalize(source: str) -> str:
    text = source.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + ("\n" if lines else "")


def _diagram_payload(
    diagram_type: str,
    source: str,
    title: str | None,
    node_count: int,
    edge_count: int,
    warnings: list[str],
    tool_name: str,
) -> str:
    return json.dumps(
        {
            "diagram_type": diagram_type,
            "mermaid": source,
            "title": title,
            "node_count": node_count,
            "edge_count": edge_count,
            "warnings": warnings,
            "artifacts": [
                {
                    "type": "mermaid",
                    "title": title,
                    "content": source,
                    "content_type": "text/vnd.mermaid",
                    "source_tool": tool_name,
                }
            ],
        },
        ensure_ascii=False,
    )
