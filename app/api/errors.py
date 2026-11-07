"""OpenAPI 中展示的统一错误响应。"""

from __future__ import annotations

from typing import Any

from app.schemas.common import ErrorResponse


def documented_errors() -> dict[int, dict[str, Any]]:
    return {
        400: {"model": ErrorResponse, "description": "模型、提示词或工具参数错误"},
        401: {"model": ErrorResponse, "description": "上游认证失败"},
        403: {"model": ErrorResponse, "description": "工具不在允许列表中"},
        404: {"model": ErrorResponse, "description": "提示词不存在"},
        422: {"model": ErrorResponse, "description": "请求参数无效，或工具轮数达到上限"},
        429: {"model": ErrorResponse, "description": "上游限流"},
        500: {"model": ErrorResponse, "description": "配置错误、工具执行失败或内部错误"},
        502: {"model": ErrorResponse, "description": "上游错误或无法解析的模型响应"},
        504: {"model": ErrorResponse, "description": "上游超时"},
    }


def stream_preflight_errors() -> dict[int, dict[str, Any]]:
    """只登记流开始前的 JSON 错误。流开始后的上游失败是 SSE error，不能写成这些状态码。"""
    return {
        400: {"model": ErrorResponse, "description": "模型不在白名单，或提示词变量缺失。此时尚未开始 SSE。"},
        404: {"model": ErrorResponse, "description": "提示词不存在。此时尚未开始 SSE。"},
        422: {"model": ErrorResponse, "description": "请求体无效、stream 为 false，或 JSON 模式缺少 json。此时尚未开始 SSE。"},
        500: {"model": ErrorResponse, "description": "未配置 API Key。此时尚未开始 SSE。"},
    }
