"""只做算术的安全计算器。使用 AST 白名单，不调用 eval 或 exec。"""

from __future__ import annotations

import ast
import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_MAX_ABS = 10**12
_MAX_EXPONENT = 8
_MAX_NODES = 80
_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.UAdd,
    ast.USub,
)
_BIN_OPS = {
    ast.Add: lambda left, right: left + right,
    ast.Sub: lambda left, right: left - right,
    ast.Mult: lambda left, right: left * right,
    ast.Div: lambda left, right: left / right,
    ast.FloorDiv: lambda left, right: left // right,
    ast.Mod: lambda left, right: left % right,
}


class CalculatorArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expression: str = Field(min_length=1, max_length=200)

    @field_validator("expression")
    @classmethod
    def _validate_expression(cls, value: str) -> str:
        validate_expression(value)
        return value


def validate_expression(expression: str) -> None:
    tree = _parse(expression)
    try:
        _evaluate_node(tree)
    except ZeroDivisionError:
        return


def calculate(expression: str) -> str:
    tree = _parse(expression)
    return _format_number(_evaluate_node(tree))


def calculator_handler(args: CalculatorArgs) -> str:
    return calculate(args.expression)


def _parse(expression: str) -> ast.Expression:
    if len(expression) > 200:
        raise ValueError("表达式过长")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("表达式不合法") from exc
    if not isinstance(tree, ast.Expression):
        raise ValueError("表达式不合法")
    nodes = list(ast.walk(tree))
    if len(nodes) > _MAX_NODES:
        raise ValueError("表达式过于复杂")
    for node in nodes:
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError("表达式不合法")
    return tree


def _evaluate_node(node: ast.AST) -> int | float:
    if isinstance(node, ast.Expression):
        return _evaluate_node(node.body)
    if isinstance(node, ast.Constant):
        return _check_number(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _evaluate_node(node.operand)
        result = value if isinstance(node.op, ast.UAdd) else -value
        return _check_number(result)
    if isinstance(node, ast.BinOp):
        left = _evaluate_node(node.left)
        right = _evaluate_node(node.right)
        if isinstance(node.op, ast.Pow):
            return _power(left, right)
        operation = _BIN_OPS.get(type(node.op))
        if operation is None:
            raise ValueError("表达式不合法")
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise ZeroDivisionError
        return _check_number(operation(left, right))
    raise ValueError("表达式不合法")


def _power(left: int | float, right: int | float) -> int | float:
    if isinstance(right, bool) or not isinstance(right, int) or isinstance(right, float):
        raise ValueError("指数超出范围")
    if right < 0 or right > _MAX_EXPONENT or abs(left) > 10**6:
        raise ValueError("指数超出范围")
    return _check_number(left**right)


def _check_number(value: Any) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("仅允许数字")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("结果无效")
    if abs(value) > _MAX_ABS:
        raise ValueError("数值超出范围")
    return value


def _format_number(value: int | float) -> str:
    checked = _check_number(value)
    if isinstance(checked, float):
        if abs(checked - round(checked)) < 1e-9:
            checked = int(round(checked))
        else:
            return f"{checked:.10f}".rstrip("0").rstrip(".")
    return str(checked)
