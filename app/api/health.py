"""存活与就绪检查。这两个接口不调用 DeepSeek。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_settings_dep
from app.core.config import Settings
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
def ready(settings: Settings = Depends(get_settings_dep)) -> ReadyResponse:
    """只检查本进程是否已配置 API Key，不发起上游请求。"""
    if settings.has_api_key():
        return ReadyResponse(status="ready")
    return ReadyResponse(status="not_ready", reason="missing_api_key")


@router.get("/", include_in_schema=False)
def root(settings: Settings = Depends(get_settings_dep)) -> dict[str, str]:
    return {"service": settings.app_name, "docs": "/docs", "health": "/health"}
