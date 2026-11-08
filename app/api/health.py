"""存活与就绪检查。这两个接口不调用 DeepSeek。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_settings_dep
from app.core.config import Settings
from app.mcp.keys import KeyConfigurationError, validate_key_config
from app.schemas.common import HealthResponse, ReadyResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="存活检查")
def health() -> HealthResponse:
    """进程存活。不访问模型供应商。"""
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    response_model=ReadyResponse,
    response_model_exclude_none=True,
    summary="就绪检查",
)
def ready(request: Request, settings: Settings = Depends(get_settings_dep)) -> ReadyResponse:
    """开发环境只检查 API Key。生产环境同时要求数据库授权已经就绪。"""
    if not settings.has_api_key():
        return ReadyResponse(status="not_ready", reason="missing_api_key")
    reason = production_readiness_reason(
        settings,
        database_ready=bool(getattr(request.app.state, "database_ready", False)),
        grants_durable=bool(getattr(request.app.state, "mcp_grants_durable", False)),
    )
    if reason is None:
        return ReadyResponse(status="ready")
    return ReadyResponse(status="not_ready", reason=reason)


def production_readiness_reason(settings: Settings, *, database_ready: bool, grants_durable: bool) -> str | None:
    if settings.runtime_env.strip().lower() != "production":
        return None
    try:
        validate_key_config(settings)
    except KeyConfigurationError:
        return "mcp_key_invalid"
    if not database_ready or not grants_durable:
        return "database_unavailable"
    return None


@router.get("/", include_in_schema=False)
def root(settings: Settings = Depends(get_settings_dep)) -> dict[str, str]:
    return {"service": settings.app_name, "docs": "/docs", "health": "/health"}
